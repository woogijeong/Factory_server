"""Plain-assert tests, no MX Component or GX Simulator2 needed:  python test_bridge.py"""
import asyncio
import copy
import os
import time

from asyncua import Client, ua

from bridge import Bridge, MockPlc, validate

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


async def _is(node, want):
    return await node.read_value() == want


async def _mem(plc, dev, want):
    return plc.mem.get(dev) == want


if __name__ == "__main__":
    test_bad_configs()
    print("bad configs rejected")
    asyncio.run(test_e2e())
    print("ALL OK")
