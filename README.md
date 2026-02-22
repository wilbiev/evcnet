# EVC-net (Last Mile Solutions) Charging Station Integration for Home Assistant

This custom integration allows you to monitor and control your EVC-net (Last Mile Solutions) charging station through Home Assistant.

## Disclaimer

**Important Notice**: This integration is a based on the code of [Platzii](https://github.com/Platzii/homeassistant-evcnet). Rewriting the code in line with Python and Home Assistant Integration development standards resulted in a large reduction. This integration adds several additional functionalities. Choices can be made out of the available cards and channels per spot which are automatically detected. The integration works for the following 50five EvcNet-endpoints:

- BELUX: (`50five-sbelux.evc-net.com`)
- DE: (`50five-sde.evc-net.com`)
- NL: (`50five-snl.evc-net.com`)
- UK (`50five-suk.evc-net.com`)

## Features

- **Buttons**: Control charging station operations (soft/hard reset, unlock connector, block/unblock)
- **Selects**: Choose card and channel
- **Sensors**: Monitor charging status, power consumption, and energy usage
- **Switch**: Start and stop charging sessions
- **Real-time updates**: Automatic polling every 60 seconds
- **Action call**: Start a charging session using an action (allows to define a specific RFID card and channel id)

## Installation

### HACS default repository (Recommended)

1. Make sure [HACS](https://hacs.xyz/) is installed
2. In HACS, search for "EVC-net (Last Mile Solutions)"
3. Open the overflow menu (⋮) and click "Download"
4. In the pop-up, you can select a specific version to install, or leave it empty to install the latest version
5. Click "Download" to install the integration
6. Restart Home Assistant

### HACS custom repository

1. Make sure [HACS](https://hacs.xyz/) is installed
2. Add this repository as a custom repository in HACS:
   - Go to HACS → Integrations → ⋮ (top right) → Custom repositories
   - Add `https://github.com/wilbiev/evcnet` as Integration
3. Click Install
4. Restart Home Assistant

### Manual Installation

1. Copy the `custom_components/evcnet` folder to your Home Assistant's `custom_components` directory
2. Restart Home Assistant

## Configuration

1. Go to Settings → Devices & Services
2. Click "+ Add Integration"
3. Search for "EVC-net (Last Mile Solutions)"
4. Enter your credentials:
   - **Base URL**: Default is `https://50five-snl.evc-net.com`
   - **Email**: Your EVC-net account email
   - **Password**: Your EVC-net account password

## Available Entities

For each charging station, the integration creates:

### Buttons

- **Soft Reset**: Perform a soft reset on the charging station
- **Hard Reset**: Perform a hard reset on the charging station
- **Unlock Connector**: Unlock the connector on the charging station
- **Block**: Block the charging station from use
- **Unblock**: Unblock the charging station to allow use

### Selects

- **Card ID**: Select your preferred card from the list
- **Channel ID**: Select your preferred channel from the list

### Sensors

- **Connector**: Shows connector name. Contains several attributes.
- **Current Power**: Active power draw in kilowatts
- **Status**: Current charging station status
- **Session Energy**: Energy consumed in current session (kWh)
- **Session Time**: Duration of current charging session in hours
- **Status Code**: Raw status code from the charging station
- **Total Energy**: Total energy consumed (kWh)
- **Last Logging Update**: shows when the logging was updated. Contains attribute **log_data** with 50 logging entries per channel. Can be converted to markdown in the dashboard to show a list.

### Switch

- **Charging**: Turn on to start charging, off to stop, includes pregit pushparation mode

## Using Multiple RFID Cards

The integration supports using different RFID cards for different charging sessions. This is useful when you have multiple vehicles with different charging cards.

### Using the Action

You can use the `evcnet.start_charging` action to specify which RFID card to use:

```yaml
action: evcnet.start_charging
target:
  entity_id: switch.your_charging_station_charging # or device_id: 516934b04b9345cb26086fdb88de6467
data:
  card_id: "ABC12DEF34" # Your RFID card ID
  channel_id: "1" # Your channel ID
```

## Tip & tricks

### Logging sensor

Exclude the sensor Last Logging Update from your recorder in `configuration.yaml`.
It prevents a severe growth of your Home Assistant database.

```yaml
recorder:
  exclude:
    entities:
      - sensor.charge_spot_123_last_logging_update
```

### Logging markdown

The following logging fields are available in attribute entries:
LOG_DATE, NOTIFICATION, EVENT_TYPE, EVENT_DATA, EVENT_SOURCE, STATUS,
MOM_POWER_KW, SOC, TRANS_ENERGY_DELIVERED_KWH, TRANSACTION_TIME_H_M,
IS_GLOBAL_EVENT, CARDID, CARD_TYPE_ICON, CUSTOMERS_IDX, CUSTOMER_NAME,
CARDS_IDX, IS_SELF, IS_GLOBAL_CARD, IDX

Example of creating of a markdown card on the dashboard to show logging (Dutch version)

```yaml
type: markdown
content: >-
  {% set sensor = 'sensor.charge_spot_xxxxxxxx_last_logging_update' %}

  {%- set logs = state_attr(sensor, 'entries') -%}

  {%- if logs -%}

  {%- set months = {'jan.': '01', 'feb.': '02', 'mrt.': '03', 'apr.': '04',
  'mei': '05', 'jun.': '06', 'jul.': '07', 'aug.': '08', 'sep.': '09', 'okt.':
  '10', 'nov.': '11', 'dec.': '12'} -%}


  ### 🕒 Laadhistorie

  > Laatste sync: **{{ as_timestamp(states(sensor)) |
  timestamp_custom('%H:%M:%S') }}**


  | Datum | Melding | Power (kW) | Energy (kWh) | Tijd |

  | :--- | :--- | :---: | :---: | :---: |

  {%- for log in logs[:20] -%}
    {%- set p = log.LOG_DATE.split('-') -%}
    {%- if p | length == 3 -%}
      {%- set dag = p[0].zfill(2) -%}
      {%- set maand = months.get(p[1].lower(), p[1]) -%}
      {%- set jt = p[2].split(' ') -%}
      {%- set jaar = jt[0][-2:] -%}
      {%- set datum = dag ~ '-' ~ maand ~ '-' ~ jaar ~ ' ' ~ jt[1][:5] -%}
    {%- else -%}
      {%- set datum = log.LOG_DATE -%}
    {%- endif -%}
  {# Weergeven geformatteerde rij #}

  | {{ datum }} | {{ log.NOTIFICATION | default('-') }} | {{ log.MOM_POWER_KW |
  default('-') }} | {{ log.TRANS_ENERGY_DELIVERED_KWH | default('-') }} | {{
  log.TRANSACTION_TIME_H_M | default('-') }} |

  {%- endfor -%}


  {%- else -%}

  ⚠️ Geen loggegevens beschikbaar.

  {%- endif -%}
```

## Troubleshooting

Enable debug logging by adding this to your `configuration.yaml`:

```yaml
logger:
  default: info
  logs:
    custom_components.evcnet: debug
```

## Support

Report issues at: https://github.com/wilbiev/evcnet/issues

## License

MIT License
