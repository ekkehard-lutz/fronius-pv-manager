# Fronius PV Manager

Fronius PV Manager is a local-polling Home Assistant custom integration for
Fronius systems that expose SunSpec over Modbus TCP. It discovers supported
inverter, storage, and Smart Meter capabilities and organizes their entities as
separate Home Assistant devices.

Version 0.2.0 is the first stable release. It provides the stable low-level
communication, discovery, sensor, and guarded register-control foundation.
Higher-level Home Assistant controls may be added separately in future releases.

## For Home Assistant users

### Features

- Local SunSpec discovery and polling over Modbus TCP.
- Inverter, storage, and Smart Meter device roles based on discovered models.
- Multiple Modbus device IDs on one configured host and port.
- Stable, language-independent entity identities with translated display names.
- Operational and diagnostic sensors backed by reviewed register definitions.
- English and German Home Assistant translations.
- Device-specific availability: one unavailable Modbus device does not hide
  healthy devices on the same endpoint.

Normal users do not need to edit register maps or the write policy. Low-level
writable entities are intended for experts and are disabled in the Entity
Registry by default.

### Requirements and compatibility

- Home Assistant 2026.8.0 or newer, as declared in `hacs.json`.
- A Fronius device with SunSpec Modbus TCP enabled.
- Network access from Home Assistant to the configured host and TCP port.

Support is capability-based. Compatibility depends on the SunSpec models and
registers exposed by the device; universal support for every Fronius product is
not claimed.

### Tested hardware and compatibility

Fronius PV Manager v0.2.0 was developed and hardware-tested with:

- Fronius Symo GEN24 10.0
- BYD Battery-Box Premium HVM 11.0
- Fronius Smart Meter TS 65A-3

These exact devices are hardware-validated. The integration discovers
capabilities from SunSpec models, registers, and their semantics rather than
primarily matching product names. Other Fronius devices exposing the same
supported models with compatible register semantics are expected to work, but
remain unverified until tested on real hardware. This is not a blanket
compatibility claim for all GEN24 inverters, BYD batteries, or Fronius Smart
Meters.

### Installation with HACS

This repository does not claim inclusion in the default HACS store. Install it
as a custom repository:

1. In HACS, open **Integrations**.
2. Open the menu and choose **Custom repositories**.
3. Add `https://github.com/ekkehard-lutz/fronius-pv-manager` with category
   **Integration**.
4. Find **Fronius PV Manager**, install it, and restart Home Assistant.

### Configuration

Before configuration, enable SunSpec Modbus TCP on the Fronius equipment using
the vendor documentation. Then in Home Assistant:

1. Open **Settings → Devices & services**.
2. Select **Add integration** and choose **Fronius PV Manager**.
3. Enter the device host, Modbus TCP port, and comma-separated Modbus device IDs.

The default port is `502`. Device IDs must be unique values from 1 through 247;
common examples are inverter ID `1` and meter ID `200`. The integration does not
perform DNS resolution while normalizing the configured host.

Discovery creates Home Assistant devices for the physical roles that are
actually present. Entities are associated with the inverter, storage, or meter
rather than merely with the SunSpec model that supplied the value. Repeated
Model 160 modules are classified at runtime as MPPT, storage charging, or
storage discharging data.

### Availability

The integration polls every 30 seconds. A discovered device is unavailable when
its current model payload cannot be read or decoded. Optional or repeated
instances that are not physically present do not create permanently unavailable
entities. With multiple device IDs, healthy devices continue updating when one
device fails; the config entry becomes unavailable only when no configured
device can be refreshed.

### Troubleshooting

- Confirm SunSpec Modbus TCP is enabled and reachable from Home Assistant.
- Verify the host, port, and Modbus device IDs against the Fronius configuration.
- Check Home Assistant logs for discovery, timeout, or policy-loading errors.
- Reload the integration after changing its write policy.
- If a sensor is missing, verify that the corresponding SunSpec model is
  actually exposed by the device.

Report reproducible issues through the
[issue tracker](https://github.com/ekkehard-lutz/fronius-pv-manager/issues).

## For advanced users: low-level register controls

NUMBER and SELECT entities expose safely representable low-level writable
registers from SunSpec Models 123 and 124. They are configuration entities and
are disabled by default in Home Assistant's Entity Registry. They are not the
normal user interface for storage or inverter control.

Three independent safety layers apply:

1. **Entity Registry state** controls whether an entity is visible in Home
   Assistant. Enabling it does not authorize a write.
2. **Installation write policy** explicitly permits or denies writes in
   `/config/fronius_pv_manager/write_policy.yaml`.
3. **Register semantics** enforce authoritative access, datatype, range, enum,
   bitfield, scaling, and representation constraints. Policy can narrow these
   constraints but cannot broaden them.

The integration copies the packaged default policy on first setup and never
overwrites an existing installation policy. Policy edits take effect after an
integration reload or Home Assistant restart. A missing, disabled, or invalid
policy fails closed: readable entities and polling remain available, but writes
are rejected before Modbus I/O. Invalid policy never falls back to permissive
defaults.

The packaged policy explicitly lists all 25 writable Model 123/124 registers.
Only Model 124 `MinRsvPct` and `ChaGriSet` are write-enabled by default. Other
supported controls require explicit operator approval.

### Storage control and forced charging/discharging

These are low-level power-window controls, not direct charge/discharge commands:

| Register | Meaning |
| --- | --- |
| `WChaMax` | SunSpec reference power used to scale `InWRte` and `OutWRte`; it is not guaranteed to be currently achievable battery power. |
| `StorCtl_Mod` | Selects which external storage-power window boundaries are active. |
| `InWRte` | Charge-side boundary as a percentage of `WChaMax`. |
| `OutWRte` | Discharge-side boundary as a percentage of `WChaMax`. |
| `ChaGriSet` | Determines whether grid energy may be used for charging. |

`StorCtl_Mod` uses these values:

| Value | Presentation | Effect |
| ---: | --- | --- |
| 0 | Automatic | Neither `InWRte` nor `OutWRte` limit is active; normal inverter control applies. |
| 1 | Charge limit active | `InWRte` applies. |
| 2 | Discharge limit active | `OutWRte` applies. |
| 3 | Charge and discharge limits active | Both power-window boundaries apply. |

Fronius storage-power sign convention:

- Negative storage power means charging.
- Positive storage power means discharging.

Conceptual hardware-validated examples:

- To force discharge at around 10% of `WChaMax`, use `StorCtl_Mod = 1` and
  `InWRte = -10%`. `OutWRte` is not active in this mode.
- To require charging of at least around 10% of `WChaMax`, use
  `StorCtl_Mod = 2` and `OutWRte = -10%`. `InWRte` is not active in this mode.
  `ChaGriSet` must permit grid charging if grid energy is required.

The charging example establishes a boundary/minimum forced-charging condition,
not an exact charging-power command. Available PV surplus can result in higher
charging power. Inverter and BMS constraints remain authoritative and can reduce
actual power. Restore `StorCtl_Mod` to `0` to return to normal automatic inverter
operation.

> **Warning:** Low-level writes can materially alter inverter and storage
> behavior. Understand every affected register, record original values, start
> conservatively, verify the result, and restore the original configuration
> after testing.

## For software developers

### Generic capability-based design

The project deliberately avoids product-name-specific code when SunSpec models
and capabilities can describe the behavior generically:

`physical device → SunSpec discovery → discovered models/registers → reviewed RegisterDefinition/model metadata → semantic role/capability mapping → entity catalog → Home Assistant entities`

A new product may work without product-specific code when it exposes already
supported models with compatible semantics. New functionality should normally
extend reviewed model, register, and capability definitions instead of adding
product-name conditionals.

Product-specific exceptions are appropriate only when actual behavior differs
from the general SunSpec definition and authoritative documentation or hardware
evidence supports the exception. Existing GEN24-specific handling follows this
rule; it is not a product-specific architecture.

### Architecture

The Home Assistant-independent model layer provides immutable `DeviceProfile`,
`RegisterDefinition`, model, repeating-block, and decoded-value types. Reviewed
register maps describe protocol semantics and neutral entity presentation
metadata.

The runtime layers are:

1. SunSpec discovery verifies the `SunS` signature and walks the model chain.
2. Model decoding converts payloads through the reviewed register definitions.
3. `ModbusTcpEndpointTransport` owns one persistent pymodbus client/socket for
   each configured host and port.
4. Bound per-device-ID views share that endpoint while attaching the device ID
   to every request.
5. The coordinator serializes polling and writes, publishes decoded snapshots,
   and preserves partial availability across device IDs.
6. The entity catalog creates sensor, NUMBER, and SELECT entities on the
   appropriate physical Home Assistant device.
7. `WriteRuntime` resolves policy and topology, while `RegisterWriter` performs
   semantic encoding, exactly one physical write, readback, and verification.

Important guarantees:

- One shared TCP client/socket per configured host and port.
- Polls and writes are serialized across bound device-ID views.
- Failed requests reset the endpoint so later operations can reconnect.
- Uncertain writes are not retried automatically.
- Every accepted entity operation performs at most one physical write.
- Readback verification is required before coordinator refresh.
- Coordinator state is never changed optimistically.
- Home Assistant exposes no arbitrary raw-register write service.

### Developer tools

Read one semantic register without write-policy dependency:

```bash
python tools/read_register.py --host 192.168.2.11 --device-id 1 --parameter 124:MinRsvPct
```

Prepare a validated write in dry-run mode:

```bash
python tools/write_register.py --host 192.168.2.11 --device-id 1 --parameter 124:MinRsvPct --value 10
```

An actual developer write additionally requires `--write` and confirmation.
These tools resolve reviewed semantic register definitions; they are not generic
raw Modbus clients.

### Contributing

Contributions for additional Fronius inverters, storage systems, Smart Meters,
and SunSpec capabilities are welcome, especially when contributors can validate
hardware unavailable to the maintainers. Acceptance still depends on reviewed
semantics, architecture, safety, tests, documentation, and supporting evidence.
See [CONTRIBUTING.md](CONTRIBUTING.md) for the branch model, validation commands,
and pull-request workflow.

### Validation

Development targets Python 3.14. Run:

```bash
python -m ruff check .
python -m pytest
git diff --check
```

## License

Fronius PV Manager is licensed under the [MIT License](LICENSE).
