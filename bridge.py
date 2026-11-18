"""OPC UA bridge: Factory I/O (OPC UA client) <-> this server <-> GX Simulator2 (MX Component).

Run with 32-bit Python:  python bridge.py [tags.yaml]
"""
import asyncio
import logging
import math
import os
import re
import socket
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yaml
from asyncua import Server, ua
from asyncua.crypto.cert_gen import setup_self_signed_certificate
from cryptography.x509.oid import ExtendedKeyUsageOID

log = logging.getLogger("bridge")
NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
ADDR_RE = re.compile(r"^([XYMD])([0-9A-F]+)$")
REFRESH_S = 1.0  # rewrite all to_plc values at least this often
ACTUTL_TYPELIB =("{D217E54E-4A26-4A76-B0AB-57166B90F9AF}", 1, 0)  # ActUtlType (32-bit); ActUtlType64 not supported


# ---------------------------------------------------------------- config
def load_config(path):
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    return validate(cfg)


def norm_addr(s):
    """'x0a' -> 'XA', 'D0100' -> 'D100'; None if it is not a device address. X/Y are hex, M/D decimal."""
    m = ADDR_RE.match(str(s).strip().upper())
    if not m:
        return None
    kind, num = m.groups()
    if kind in "XY":
        return kind + format(int(num, 16), "X")
    return kind + str(int(num)) if num.isdigit() else None


def validate(cfg):
    plc, opcua = cfg.setdefault("plc", {}), cfg.setdefault("opcua", {})
    plc.setdefault("driver", "mx")
    plc.setdefault("station", 1)
    plc.setdefault("progid", "ActUtlType.ActUtlType")
    plc.setdefault("ladder", {})  # mock only: {output: input}, e.g. {Y0: M0}
    opcua.setdefault("endpoint", "opc.tcp://127.0.0.1:4840")
    opcua.setdefault("namespace", "urn:factory-bridge")
    cfg.setdefault("cycle_ms", 100)
    if plc["driver"] not in ("mx", "mock"):
        raise ValueError(f"plc.driver must be mx or mock, got {plc['driver']!r}")
    names, addrs = set(), set()
    for t in cfg.get("tags") or []:
        n = t.get("name")
        if not isinstance(n, str) or not NAME_RE.match(n):
            raise ValueError(f"tag {n!r}: invalid name")
        if n in names:
            raise ValueError(f"tag {n!r}: duplicate name")
        names.add(n)
        addr = norm_addr(t.get("address", ""))  # normalised, so D0100 and D100 count as the same device
        if not addr:
            raise ValueError(f"tag {n!r}: bad address {t.get('address')!r}")
        t["address"] = addr
        want = ("word", "float") if addr[0] == "D" else ("bit",)
        if t.get("type") not in want:
            raise ValueError(f"tag {n!r}: {t['address']} must be type {' or '.join(want)}, got {t.get('type')!r}")
        if t["type"] == "float":
            # float node <-> int16 device: PLC value = round(node value * scale)
            sc = t.setdefault("scale", 1)
            if isinstance(sc, bool) or not isinstance(sc, (int, float)) or not math.isfinite(sc) or sc <= 0:
                raise ValueError(f"tag {n!r}: scale must be a number > 0, got {sc!r}")
        elif "scale" in t:
            raise ValueError(f"tag {n!r}: scale is only for type float")
        if t.get("direction") not in ("to_plc", "from_plc"):
            raise ValueError(f"tag {n!r}: direction must be to_plc or from_plc, got {t.get('direction')!r}")
        if t["address"] in addrs:
            raise ValueError(f"tag {n!r}: duplicate address {t['address']}")
        addrs.add(t["address"])
    if not names:
        raise ValueError("no tags defined")
    hb = cfg.get("heartbeat")
    if hb is not None:  # optional: the bridge counts up in a D device so the ladder can detect a dead bridge
        if not isinstance(hb, dict):
            raise ValueError("heartbeat must be a mapping with address and period_ms")
        addr = norm_addr(hb.get("address", ""))
        if not addr or addr[0] != "D":
            raise ValueError(f"heartbeat: address must be a D device, got {hb.get('address')!r}")
        if addr in addrs:
            raise ValueError(f"heartbeat: address {addr} is already used by a tag")
        hb["address"] = addr
        pm = hb.setdefault("period_ms", 500)
        if isinstance(pm, bool) or not isinstance(pm, (int, float)) or not math.isfinite(pm) or pm <= 0:
            raise ValueError(f"heartbeat: period_ms must be a number > 0, got {pm!r}")
    if len(names) > 256:
        log.warning("%d tags: Factory I/O browses at most 256 nodes", len(names))
    return cfg


# ---------------------------------------------------------------- PLC drivers
# Driver interface: executor, open(), read(devs)->list[int], write(devs, vals), close(), shutdown()
class MxPlc:
    def __init__(self, station, progid, **_):
        import comtypes  # lazy: bridge imports on any OS
        self._ct = comtypes
        self.station, self.progid = station, progid
        self.com = None
        self.executor = ThreadPoolExecutor(1, initializer=comtypes.CoInitialize)  # COM stays on this one thread

    def open(self):
        import comtypes.client as cc
        mod = cc.GetModule(ACTUTL_TYPELIB)
        com = cc.CreateObject(self.progid, interface=mod.IActUtlType)
        com.ActLogicalStationNumber = self.station
        rc = com.Open()
        if rc:
            raise IOError(f"MX Open failed: 0x{rc & 0xFFFFFFFF:08X}")
        self.com = com
        # the typelib declares the buffer as short*, so call the raw vtable methods with a c_short array
        self._rd = com._IActUtlType__com_ReadDeviceRandom2
        self._wr = com._IActUtlType__com_WriteDeviceRandom2

    def _call(self, fn, devs, buf):
        import ctypes
        rc = ctypes.c_long()
        fn("\n".join(devs), len(devs), buf, ctypes.byref(rc))
        if rc.value:
            raise IOError(f"MX call failed: 0x{rc.value & 0xFFFFFFFF:08X}")

    def read(self, devs):
        import ctypes
        buf = (ctypes.c_short * len(devs))()
        self._call(self._rd, devs, buf)
        return list(buf)

    def write(self, devs, vals):
        import ctypes
        self._call(self._wr, devs, (ctypes.c_short * len(devs))(*vals))

    def close(self):
        com, self.com = self.com, None
        if com:
            com.Close()

    def shutdown(self):
        try:
            self.executor.submit(self.close).result(timeout=5)
            self.executor.submit(self._ct.CoUninitialize).result(timeout=5)
        finally:
            self.executor.shutdown(wait=True)


class MockPlc:
    """In-memory PLC. ladder={'Y0': 'M0'} copies M0 to Y0 on every read, like a one-rung program."""

    def __init__(self, ladder=None, **_):
        self.mem, self.ladder = {}, ladder or {}
        self.fail_until = 0.0  # fault injection: raise while time.monotonic() < fail_until
        self.reads = self.writes = 0
        self.executor = ThreadPoolExecutor(1)

    def _check(self):
        if time.monotonic() < self.fail_until:
            raise IOError("mock PLC unavailable")

    def open(self):
        self._check()

    def read(self, devs):
        self._check()
        self.reads += 1
        for out, inp in self.ladder.items():
            self.mem[out] = self.mem.get(inp, 0)
        return [self.mem.get(d, 0) for d in devs]

    def write(self, devs, vals):
        self._check()
        self.writes += 1
        self.mem.update(zip(devs, vals))

    def close(self):
        pass

    def shutdown(self):
        self.executor.shutdown(wait=True)


def make_driver(plc):
    return MxPlc(**plc) if plc["driver"] == "mx" else MockPlc(ladder=plc.get("ladder"))


# ---------------------------------------------------------------- bridge
class Bridge:
    def __init__(self, cfg, driver=None):
        self.cfg = cfg
        self.driver = driver or make_driver(cfg["plc"])
        self.to_tags = [t for t in cfg["tags"] if t["direction"] == "to_plc"]
        self.from_tags = [t for t in cfg["tags"] if t["direction"] == "from_plc"]
        self.to_devs = [t["address"] for t in self.to_tags]
        self.from_devs = [t["address"] for t in self.from_tags]
        self.server = None
        self.durations = []
        self._last_w, self._w_at = None, 0.0  # last values written to the PLC and when
        self._raw = {}  # last raw PLC value pushed to each from_plc node
        self._bad = {}  # to_plc values already warned about (not finite / out of range)
        hb = cfg.get("heartbeat")
        self._hb_dev = [hb["address"]] if hb else []
        self._hb_period = hb["period_ms"] / 1000 if hb else 0.0
        self._hb, self._hb_at = 0, 0.0  # heartbeat counter 0..32767 and when it last changed
        self._stop = False

    async def _plc(self, fn, *a):
        return await asyncio.get_running_loop().run_in_executor(self.driver.executor, fn, *a)

    @staticmethod
    def _vt(t):
        return {"bit": ua.VariantType.Boolean, "word": ua.VariantType.Int32, "float": ua.VariantType.Float}[t["type"]]

    @staticmethod
    def _py(t, v):  # raw PLC value -> node value
        if t["type"] == "float":
            return v / t["scale"]
        return bool(v) if t["type"] == "bit" else int(v)

    def _raw16(self, t, v):  # node value -> raw PLC value (int16)
        name, why = t["name"], None
        if t["type"] == "float" and not math.isfinite(v):
            raw, why = 0, "is not a finite number, writing 0"
        else:
            want = round(v * t["scale"]) if t["type"] == "float" else int(v)
            raw = max(-32768, min(32767, want))
            if raw != want:
                why = f"needs {want} in the PLC, out of int16 range, clamped"
        if why is None:
            self._bad.pop(name, None)
        elif self._bad.get(name) != repr(v):  # this runs every cycle: warn once per offending value
            self._bad[name] = repr(v)
            log.warning("%s=%r %s", name, v, why)
        return raw

    async def start(self):
        for attempt in range(60):  # MX Open can fail for up to ~1 min after a client was killed without Close()
            try:
                await self._plc(self.driver.open)
                break
            except OSError as e:
                if attempt == 59:
                    raise
                log.warning("PLC open failed (%s), retrying", e)
                await asyncio.sleep(1)
        devs = self.to_devs + self.from_devs
        seed = dict(zip(devs, await self._plc(self.driver.read, devs)))  # seed nodes so first cycle doesn't write zeros
        s = self.server = Server()
        await s.init()
        s.set_endpoint(self.cfg["opcua"]["endpoint"])
        # Factory I/O's .NET client fails with "server did not return the certificate" unless the server has one,
        # so offer None plus signed policies, all with a self-signed cert (generated once into ./certs).
        uri = "urn:factory-bridge:server"
        await s.set_application_uri(uri)
        certs = Path(__file__).with_name("certs")
        certs.mkdir(exist_ok=True)
        key, cert = certs / "server_key.pem", certs / "server_cert.der"
        await setup_self_signed_certificate(key, cert, uri, socket.gethostname(),
                                            [ExtendedKeyUsageOID.SERVER_AUTH, ExtendedKeyUsageOID.CLIENT_AUTH],
                                            {"commonName": "factory-bridge"})
        await s.load_certificate(cert)
        await s.load_private_key(key)
        s.set_security_policy([ua.SecurityPolicyType.NoSecurity,
                               ua.SecurityPolicyType.Basic256Sha256_Sign,
                               ua.SecurityPolicyType.Basic256Sha256_SignAndEncrypt])
        idx = await s.register_namespace(self.cfg["opcua"]["namespace"])
        self.nodes = {}
        for t in self.cfg["tags"]:
            n = t["name"]
            node = await s.nodes.objects.add_variable(
                ua.NodeId(n, idx), ua.QualifiedName(n, idx), ua.Variant(self._py(t, seed[t["address"]]), self._vt(t)))
            if t["direction"] == "to_plc":
                await node.set_writable()
            else:
                self._raw[n] = seed[t["address"]]
            self.nodes[n] = node
        await s.start()
        log.info("OPC UA server on %s, %d tags", self.cfg["opcua"]["endpoint"], len(self.nodes))

    async def cycle(self):
        # A. OPC UA -> PLC: write ALL to_plc values (Factory I/O owns them)
        vals = [await self.nodes[t["name"]].read_value() for t in self.to_tags]
        ints = [self._raw16(t, v) for v, t in zip(vals, self.to_tags)]
        now = time.monotonic()
        if self._hb_dev and now - self._hb_at >= self._hb_period:
            self._hb, self._hb_at = (self._hb + 1) % 32768, now
        out = ints + [self._hb] * len(self._hb_dev)  # the heartbeat rides in the same write call
        # Each MX call costs ~38 ms (the simulator serialises them), so write only on change, plus a 1 s refresh
        # so a PLC reset or a manual edit of a to_plc address heals within a second.
        if out and (out != self._last_w or now - self._w_at > REFRESH_S):
            await self._plc(self.driver.write, self.to_devs + self._hb_dev, out)
            self._last_w, self._w_at = out, now
        # B. PLC -> OPC UA: read ALL from_plc values, push changes
        if self.from_tags:
            got = await self._plc(self.driver.read, self.from_devs)
            for t, v in zip(self.from_tags, got):
                if v != self._raw[t["name"]]:  # from_plc nodes are read-only for clients, so the raw value is enough
                    new = self._py(t, v)
                    await self.nodes[t["name"]].write_value(ua.Variant(new, self._vt(t)))
                    self._raw[t["name"]] = v
                    self._changed(t["name"], new)
        return ints

    def _changed(self, name, v):
        if self._t_in is not None:
            log.info("round trip input->%s=%s: %.0f ms", name, v, (time.monotonic() - self._t_in) * 1000)

    async def run(self):
        cycle = self.cfg["cycle_ms"] / 1000
        last_in, self._t_in, last_log = None, None, time.monotonic()
        while not self._stop:
            t0 = time.monotonic()
            try:
                ins = await self.cycle()
                if ins != last_in:
                    last_in, self._t_in = ins, t0  # remember when Factory I/O last changed an input
            except Exception as e:  # PLC gone: keep serving stale values, retry open every 1 s
                log.error("PLC error: %s; reconnecting", e)
                await self._reconnect()
                continue
            dt = time.monotonic() - t0
            self.durations.append(dt)
            if t0 - last_log > 10:
                d = sorted(self.durations)
                log.info("cycle p95=%.0f ms max=%.0f ms (target %.0f)", d[int(len(d) * .95) - 1 if len(d) > 1 else 0] * 1000,
                         d[-1] * 1000, cycle * 1000)
                self.durations, last_log = [], t0
            await asyncio.sleep(max(0, cycle - dt))

    async def _reconnect(self):
        self._last_w = None  # force a full write after the PLC is back
        while not self._stop:
            await asyncio.sleep(1)
            try:
                await self._plc(self.driver.close)
                await self._plc(self.driver.open)
                log.info("PLC reconnected")
                return
            except Exception as e:
                log.error("reconnect failed: %s", e)

    async def stop(self):
        self._stop = True
        if self.server:
            await self.server.stop()
        await asyncio.get_running_loop().run_in_executor(None, self.driver.shutdown)


async def amain(path):
    b = Bridge(load_config(path))
    await b.start()
    try:
        await b.run()
    finally:
        await b.stop()


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("asyncua").setLevel(logging.INFO if os.environ.get("BRIDGE_DEBUG") else logging.WARNING)
    try:
        asyncio.run(amain(sys.argv[1] if len(sys.argv) > 1 else "tags.yaml"))
    except KeyboardInterrupt:
        log.info("stopped")
    except ValueError as e:
        sys.exit(f"config error: {e}")
    except OSError as e:  # startup only; once running, PLC errors are retried
        sys.exit(f"PLC error: {e} (is the GX Simulator2 simulation running?)")


if __name__ == "__main__":
    main()
