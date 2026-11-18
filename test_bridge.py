"""Plain-assert tests, no MX Component or GX Simulator2 needed:  python test_bridge.py"""
import asyncio
import copy
import os
import tempfile
import time
from pathlib import Path

from asyncua import Client, ua

import gen_tags
from bridge import Bridge, MockPlc, load_config, validate

PORT = 4851
URL = f"opc.tcp://127.0.0.1:{PORT}"
CFG = {
    "plc": {"driver": "mock"},
    "opcua": {"endpoint": URL},
    "cycle_ms": 50,
    "tags": [
        {"name": "i_sensor", "address": "M0", "type": "bit", "direction": "to_plc"},
        {"name": "o_motor", "address": "Y0", "type": "bit", "direction": "from_plc"},
        {"name": "o_count", "address": "D100", "type": "word", "direction": "from_plc"},
        {"name": "i_weight", "address": "D200", "type": "float", "scale": 100, "direction": "to_plc"},
        {"name": "o_level", "address": "D201", "type": "float", "scale": 100, "direction": "from_plc"},
    ],
}


def test_bad_configs():
    def bad(mutate, word):
        c = copy.deepcopy(CFG)
        mutate(c)
        try:
            validate(c)
        except ValueError as e:
            assert word in str(e), e
        else:
            raise AssertionError(f"accepted bad config ({word})")

    bad(lambda c: c["tags"].append(dict(c["tags"][0])), "duplicate name")
    bad(lambda c: c["tags"][1].update(type="word"), "o_motor")
    bad(lambda c: c["tags"][0].update(direction="both"), "i_sensor")
    bad(lambda c: c["tags"].append({"name": "x", "address": "M0", "type": "bit", "direction": "to_plc"}), "duplicate address")
    bad(lambda c: c["tags"][0].update(type="float"), "i_sensor")  # float needs a D device
    bad(lambda c: c["tags"][3].update(scale=0), "scale")
    bad(lambda c: c["tags"][3].update(scale="x"), "scale")
    bad(lambda c: c["tags"][2].update(scale=10), "only for type float")
    bad(lambda c: c.update(heartbeat={"address": "M10"}), "heartbeat")
    bad(lambda c: c.update(heartbeat={"address": "D100"}), "already used")
    bad(lambda c: c.update(heartbeat={"address": "D9500", "period_ms": 0}), "period_ms")
    bad(lambda c: c.update(heartbeat={"address": "D9500", "period_ms": float("nan")}), "period_ms")
    bad(lambda c: c.update(heartbeat={"address": "D9500", "period_ms": float("inf")}), "period_ms")
    bad(lambda c: c.update(heartbeat={"address": "D0100"}), "already used")  # same device as D100
    bad(lambda c: c.update(heartbeat={"address": "D1F"}), "heartbeat")  # D is decimal
    bad(lambda c: c["tags"][2].update(address="D1F"), "bad address")
    bad(lambda c: c["tags"].append({"name": "x", "address": "M00", "type": "bit", "direction": "to_plc"}), "duplicate address")
    c = copy.deepcopy(CFG)
    c["tags"][1]["address"] = "y00a"  # X/Y are hex; leading zeros and case are normalised
    assert validate(c)["tags"][1]["address"] == "YA"
    ok = validate(copy.deepcopy(CFG) | {"heartbeat": {"address": "d9500"}})
    assert ok["heartbeat"] == {"address": "D9500", "period_ms": 500}, ok["heartbeat"]
    c = copy.deepcopy(CFG)
    del c["tags"][3]["scale"]
    assert validate(c)["tags"][3]["scale"] == 1  # scale defaults to 1


def test_gen_tags():
    with tempfile.TemporaryDirectory() as d:
        src, dst = Path(d, "io.csv"), Path(d, "tags.yaml")
        # Korean headers, cp949 (what Excel on Korean Windows saves), blanks filled from the device letter
        src.write_bytes("이름,주소,타입,방향,배율,비고\n# 건너뛰는 줄\nFIO_X20,x20,,,,센서\n,Y20,,,,\n"
                        "FIO_W,D5000,,입력,100,무게\nWEB_ST,D1900,,from_plc,,\n".encode("cp949"))
        dst.write_text("plc:\n  driver: mock\nheartbeat: {address: D9500}\n"
                       "tags:\n  - {name: OLD, address: M0, type: bit, direction: to_plc}\n", encoding="utf-8")
        gen_tags.main(src, dst)
        cfg = load_config(dst)
        assert cfg["plc"]["driver"] == "mock" and cfg["heartbeat"]["address"] == "D9500", cfg  # header kept
        assert cfg["tags"] == [
            {"name": "FIO_X20", "address": "X20", "type": "bit", "direction": "to_plc"},
            {"name": "Y20", "address": "Y20", "type": "bit", "direction": "from_plc"},
            {"name": "FIO_W", "address": "D5000", "type": "float", "direction": "to_plc", "scale": 100},
            {"name": "WEB_ST", "address": "D1900", "type": "word", "direction": "from_plc"},
        ], cfg["tags"]
        assert "OLD" in Path(str(dst) + ".bak").read_text(encoding="utf-8")  # previous file kept as .bak
        # a bad list changes nothing: M needs a direction; duplicate address is caught by the bridge's own checks
        before = dst.read_text(encoding="utf-8")
        cases = (("name,address\nA,M5\n", "direction"), ("name,address\nA,X0\nB,X0\n", "duplicate address"),
                 ("name,address\n# note\n\nA,X0\nB,\n", "line 5: address is empty"),  # the real CSV line number
                 ("태그명,주소,이름\nFIO_A,X0,\n", "more than one column for: name"))
        for body, word in cases:
            src.write_text(body, encoding="utf-8")
            try:
                gen_tags.main(src, dst)
            except ValueError as e:
                assert word in str(e), e
            else:
                raise AssertionError(f"accepted bad I/O list ({word})")
            assert dst.read_text(encoding="utf-8") == before
        # settings written below the tag list would be dropped, so the generator refuses instead
        dst.write_text(before + "cycle_ms: 20\n", encoding="utf-8")
        src.write_text("name,address\nA,X0\n", encoding="utf-8")
        try:
            gen_tags.main(src, dst)
        except ValueError as e:
            assert "cycle_ms" in str(e), e
        else:
            raise AssertionError("dropped a setting below the tag list")
        # values YAML would misread survive: a name like ON, a tiny scale, a numbered first column, odd comments
        dst.unlink()
        src.write_text('No,name,address,direction,scale,comment\n#1,ON,X0,,,"a: b # c\nline2"\n#2,W,D5,in,0.00001,{x}\n', encoding="utf-8")
        gen_tags.main(src, dst)
        tags = load_config(dst)["tags"]
        assert [(t["name"], t["address"]) for t in tags] == [("ON", "X0"), ("W", "D5")], tags
        assert tags[1]["scale"] == 0.00001, tags[1]
        assert "# a: b # c line2" in dst.read_text(encoding="utf-8")


async def until(cond, timeout):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if await cond():
            return time.monotonic() - t0
        await asyncio.sleep(0.005)
    raise AssertionError(f"not true within {timeout}s")


async def test_e2e():
    plc = MockPlc(ladder={"Y0": "M0"})
    b = Bridge(validate(copy.deepcopy(CFG)), driver=plc)
    await b.start()
    task = asyncio.create_task(b.run())
    try:
        # endpoint advertised == the URL we connect to, security None
        eps = await Client(URL).connect_and_get_server_endpoints()
        assert eps and all(e.EndpointUrl == URL and e.ServerCertificate for e in eps), eps  # cert returned on every endpoint
        assert any(e.SecurityMode == ua.MessageSecurityMode.None_ for e in eps), eps
        async with Client(URL) as c:
            idx = await c.get_namespace_index("urn:factory-bridge")
            node = lambda n: c.get_node(ua.NodeId(n, idx))
            sensor, motor, count = node("i_sensor"), node("o_motor"), node("o_count")
            assert await sensor.read_value() is False and await motor.read_value() is False
            # gate: sensor ON -> motor ON within 200 ms end to end, and OFF again
            for want in (True, False, True, False):
                await sensor.write_value(ua.Variant(want, ua.VariantType.Boolean))
                dt = await until(lambda: _is(motor, want), 0.2)
            print(f"  end-to-end ok (last {dt * 1000:.0f} ms)")
            # signed word
            plc.mem["D100"] = -5
            await until(lambda: _is(count, -5), 0.5)
            # float tags: node is Float, PLC holds round(value * scale) as int16
            weight, level = node("i_weight"), node("o_level")
            assert await weight.read_data_type_as_variant_type() == ua.VariantType.Float
            assert await level.read_data_type_as_variant_type() == ua.VariantType.Float
            for val, raw in ((14.25, 1425), (-1.5, -150), (0.004, 0), (400.0, 32767), (float("nan"), 0)):
                await weight.write_value(ua.Variant(val, ua.VariantType.Float))
                await until(lambda: _mem(plc, "D200", raw), 0.5)
            for raw, val in ((750, 7.5), (-25, -0.25), (0, 0.0)):
                plc.mem["D201"] = raw
                await until(lambda: _near(level, val), 0.5)
            try:
                await level.write_value(ua.Variant(1.0, ua.VariantType.Float))
            except ua.UaStatusCodeError:
                pass
            else:
                raise AssertionError("write to from_plc float node succeeded")
            await weight.write_value(ua.Variant(0.0, ua.VariantType.Float))
            await until(lambda: _mem(plc, "D200", 0), 0.5)
            # per-hop timing: informational, hard fail only above 150 ms
            hops = []
            for i in range(20):
                want = i % 2 == 0
                await sensor.write_value(ua.Variant(want, ua.VariantType.Boolean))
                hops.append(await until(lambda: _is(motor, want), 0.5))
            hops.sort()
            p95, worst = hops[int(len(hops) * .95) - 1], hops[-1]
            print(f"  round trip p95={p95 * 1000:.0f} ms max={worst * 1000:.0f} ms")
            assert worst < .15 or os.environ.get("BRIDGE_LAX"), "round trip above 150 ms"
            # from_plc nodes are read-only for clients
            try:
                await motor.write_value(ua.Variant(True, ua.VariantType.Boolean))
            except ua.UaStatusCodeError:
                pass
            else:
                raise AssertionError("write to from_plc node succeeded")
            # <=1 read and <=1 write per cycle
            r0, w0, t0 = plc.reads, plc.writes, time.monotonic()
            await asyncio.sleep(1)
            cycles = (time.monotonic() - t0) / 0.05 + 1
            assert plc.reads - r0 <= cycles and plc.writes - w0 <= cycles, (plc.reads - r0, plc.writes - w0, cycles)
            # idle: values unchanged -> far fewer writes than cycles; a PLC reset still heals within the 1 s refresh
            await sensor.write_value(ua.Variant(True, ua.VariantType.Boolean))
            await until(lambda: _is(motor, True), 0.5)
            w0 = plc.writes
            await asyncio.sleep(0.5)
            assert plc.writes - w0 <= 1, plc.writes - w0
            plc.mem["M0"] = 0  # simulated PLC reset
            await until(lambda: _mem(plc, "M0", 1), 1.5)
            await sensor.write_value(ua.Variant(False, ua.VariantType.Boolean))
            await until(lambda: _is(motor, False), 0.5)
            # reconnect: PLC down for 2 s, then recovers within 3 s and sync works again
            plc.fail_until = time.monotonic() + 2
            await asyncio.sleep(0.3)
            await sensor.write_value(ua.Variant(True, ua.VariantType.Boolean))
            await until(lambda: _is(motor, True), 5)
            await sensor.write_value(ua.Variant(False, ua.VariantType.Boolean))
            await until(lambda: _is(motor, False), 0.5)
    finally:
        await b.stop()
        task.cancel()


async def test_heartbeat():
    """The bridge counts up in D9500 every period, also with no to_plc tags, wraps at 32767 and survives a PLC outage."""
    cfg = {"plc": {"driver": "mock"}, "opcua": {"endpoint": URL}, "cycle_ms": 20,
           "heartbeat": {"address": "D9500", "period_ms": 100},
           "tags": [{"name": "o_motor", "address": "Y0", "type": "bit", "direction": "from_plc"}]}
    plc = MockPlc()
    b = Bridge(validate(cfg), driver=plc)
    await b.start()
    task = asyncio.create_task(b.run())
    try:
        await until(lambda: _mem_ge(plc, "D9500", 1), 0.5)
        seen, t0 = [], time.monotonic()
        while time.monotonic() - t0 < 1.0:
            seen.append(plc.mem["D9500"])
            await asyncio.sleep(0.005)
        steps = [b_ - a for a, b_ in zip(seen, seen[1:]) if b_ != a]
        assert steps and all(d == 1 for d in steps), steps  # counts up by one, never skips or goes back
        assert 7 <= len(steps) <= 11, len(steps)  # ~10 beats per second at period_ms 100
        b._hb = 32766  # wrap: 32767 -> 0, never negative
        await until(lambda: _mem(plc, "D9500", 32767), 0.5)
        await until(lambda: _mem(plc, "D9500", 0), 0.5)
        await until(lambda: _mem(plc, "D9500", 1), 0.5)
        # PLC down: the counter in the PLC stops; after the PLC is back it moves again
        plc.fail_until = time.monotonic() + 1.5
        await asyncio.sleep(0.3)
        frozen = plc.mem["D9500"]
        await asyncio.sleep(0.5)
        assert plc.mem["D9500"] == frozen, (plc.mem["D9500"], frozen)
        await until(lambda: _mem_ne(plc, "D9500", frozen), 5)
    finally:
        await b.stop()
        task.cancel()
    print("  heartbeat ok")


async def _is(node, want):
    return await node.read_value() == want


async def _near(node, want):
    return abs(await node.read_value() - want) < 1e-6


async def _mem_ge(plc, dev, want):
    return plc.mem.get(dev, 0) >= want


async def _mem_ne(plc, dev, other):
    return plc.mem.get(dev) != other


async def _mem(plc, dev, want):
    return plc.mem.get(dev) == want


if __name__ == "__main__":
    test_bad_configs()
    print("bad configs rejected")
    test_gen_tags()
    print("gen_tags ok")
    asyncio.run(test_e2e())
    asyncio.run(test_heartbeat())
    print("ALL OK")
