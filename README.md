# Fronius PV Manager

## Status

Fronius PV Manager is under active development and is not yet ready for
production use. The current code establishes the integration metadata and a
tested core data model; device communication and Home Assistant entities are
planned functionality.

## Purpose

This custom integration will provide local Home Assistant access to compatible
Fronius equipment through SunSpec Modbus TCP. Planned device classes include
Fronius inverters, battery storage systems, and Fronius Smart Meters.

Compatibility will depend on the SunSpec models and capabilities a device
exposes. Universal compatibility with every Fronius product is not claimed.

## Architecture

The planned data path is:

`Home Assistant → Fronius PV Manager → Fronius/SunSpec core → SunSpec model definitions → Modbus TCP → physical Fronius equipment`

Discovery will derive features from available SunSpec models and registers.
This capability-based approach avoids tying behavior to exact product names.

Definitions describe possible data, not guaranteed entities. A register
definition does not automatically produce a Home Assistant entity, and a
theoretically possible repeated block does not mean that a physical device
implements that instance. Future runtime discovery will select the repeated
instances that are actually present. Absent optional or repeated instances
should not create permanent unavailable entities; unavailable is reserved for
an entity considered present whose current value cannot be read or decoded.

## Register maps

Fronius Excel register maps are development inputs only. End users will not be
required to upload Excel files: the integration will ship reviewed register
definitions. Support for new maps or models will arrive through integration
updates and feature requests.

## Read/write safety

Writable Modbus registers will not automatically become unrestricted raw write
endpoints. Future high-level controllers will validate register combinations,
perform writes safely, and verify results. These safeguards are especially
important for battery and storage control.

The independent core provides symmetrical register decoding and encoding.
Encoding validates write access, semantic ranges, scale factors, exact integer
representation, and invalid SunSpec sentinels. The entity catalog exposes
reviewed writable numeric and enum registers as low-level number and select
entities; Home Assistant provides no arbitrary register-write service.

### Installation write policy

`RegisterDefinition` remains the hard manufacturer and protocol constraint.
Home Assistant writes additionally require an explicit installation policy at
`/config/fronius_pv_manager/write_policy.yaml`. A register absent from that
file is not writable through the Home Assistant runtime. Policy ranges, enum
choices, and bit masks may narrow documented constraints but can never broaden
them or bypass the encoder.

Catalog exposure and write permission are independent. Low-level controls are
disabled by default in the Home Assistant Entity Registry. Enabling one there
only makes it visible; it does not grant Modbus write permission. A missing
policy entry leaves the entity readable but write-protected. An entry may set
`enabled: false` to temporarily deny writes while retaining and validating its
configured constraints. Omitting `enabled` is equivalent to `enabled: true`.

The shipped default policy approves only Model 124 `MinRsvPct` from 0 through
100 percent and `ChaGriSet` for its documented PV-disabled and GRID-enabled
choices. All other writable registers remain unapproved.

On first config-entry setup, the integration copies its conservative packaged
default to the installation path. Existing installation policy is never
overwritten, so it survives HACS updates. The complete YAML file is safely
parsed and validated only during config-entry setup. Editing it requires an
integration reload or Home Assistant restart. An invalid existing policy fails
closed: polling and readable catalog controls continue, but that config entry
receives no approved writes and the packaged default is not substituted.

Each physical register has one platform selected by the catalog: read-only
values are sensors, writable numeric controls are numbers, and writable enum
controls are selects. Policy permission affects writes, not entity existence.
Number controls are exposed only when their register definition supplies finite
authoritative bounds; no generic or guessed limits are used. An enabled policy
may narrow those hard bounds or documented enum options. Policy edits take
effect after an integration reload or Home Assistant restart.

### Developer register write testing

`tools/write_register.py` is a developer diagnostic utility, not a general raw
Modbus writer. It accepts only qualified, fixed registers marked writable by a
reviewed definition. Values pass through `encode_register_value`, including
access, range, enum, scaling, representation, and sentinel validation. The
tool always reads the current value before a possible write and verifies the
decoded value afterward.

The preparation, encoding, single-write, read-back, and semantic verification
workflow lives in reusable Home Assistant-independent core code. The CLI adds
only dry-run policy, confirmation, localization, formatting, and exit status.
Home Assistant number and select entities use the same verified core path.

Dry run is the default and never modifies the device:

```powershell
python tools\write_register.py `
  --host 192.168.2.11 `
  --device-id 1 `
  --parameter 124:MinRsvPct `
  --value 10
```

An actual write requires `--write` and interactive confirmation:

```powershell
python tools\write_register.py `
  --host 192.168.2.11 `
  --device-id 1 `
  --parameter 124:MinRsvPct `
  --value 10 `
  --write
```

For automated developer testing, `--yes` bypasses only confirmation; it does
not authorize a write without `--write`:

```powershell
python tools\write_register.py `
  --host 192.168.2.11 `
  --device-id 1 `
  --parameter 124:MinRsvPct `
  --value 10 `
  --write `
  --yes
```

## Localization

English and German localization are supported from the beginning. User-visible
text will live in Home Assistant translation files rather than in Python logic.

## Entity catalog

Register maps contain a Home Assistant-independent catalog for values intended
to become entities. Its four independent dimensions are the future platform,
presentation category, whether the entity is enabled by default, and its
physical device role. A register definition does not automatically imply an
entity, and a disabled-by-default entity does not mean its register is
unsupported. Raw register metadata remains visible in developer inspection.

Home Assistant will later consume this catalog. Model 160 repeated values keep
their device role unset because runtime semantic classification determines
whether each concrete module belongs to the inverter or storage device.

Optional `help_text` metadata provides localized, project-maintained
explanations while technical register descriptions remain English and
unchanged. English is the canonical fallback; additional translations are
optional. The inspector accepts `--info MODEL_ID:NAME --lang LANGUAGE`.
Future Home Assistant presentation can use the same fallback helper to select
text from the current user-interface language.

The standalone developer inspector supports complementary views:

```text
python tools/inspect_device.py --host 192.168.2.11 --decode
python tools/inspect_device.py --host 192.168.2.11 --decode --all
python tools/inspect_device.py --info 124:ChaState
python tools/inspect_device.py --info 124:ChaState --lang de
```

`--decode` is the concise operational view containing enabled-by-default
catalog entities. Adding `--all` shows the complete technical decoded model.
`--info` independently looks up metadata and help for any known register,
regardless of whether it is cataloged or enabled.

## Development

Development targets Python 3.12. Run `ruff check .` for linting and `pytest -q`
for unit tests.

## License

This project is licensed under the MIT License.
