#  Domoticz_Marstek_Modbus

Connecting Marstek Venus V3 home battery over Modbus to Domoticz.

No need for stupid openApi / MQTT stuff


<b>Prerequisites :</b>
  * Home battery Marstek Venus V3 running with modbus port and cable.
  * A DR134 module (I used a PUSR-DR134)
  * Install pymodbus python plugin ( pip install pymodbus )

<b>Result : </b>
<img width="1217" height="580" alt="marstek overview" src="https://github.com/user-attachments/assets/96949b4f-874e-4d94-b2b3-e69ce949046f" />

----

For the DR134 settings I used:
* Work mode TCP server
* Baud rate : 115200
* Data Size : 8 bit
* Parity: none
* Stop bits : 1

In my case (using the PUSR-DR134) in EDGE gateway tab:
* working mode: Modbus Simple protocol

I guess any DR134 device that simply converts modbus RTU data > TCP protocol data will work


----
Changelog 1.3:
- TCP connection failure is now distinguished from an RS485/Modbus failure.
  - If TCP connects but register 30000 gets no response / 0 bytes, the Connection Status becomes: Gateway connected / No Modbus reply
- Modbus Exception 02 / Illegal Data Address is identified separately. This tells us communication works, but that particular register isn't supported by the battery model/firmware.
- Error messages now distinguish:
  - Gateway/network unreachable
  - Gateway reachable but battery/RS485 not replying
  - Unsupported register
  - Other Modbus errors
- Troubleshooting for a no-response condition now specifically mentions:
  - Slave ID
  - 115200 8N1
  - RS485 A/B
  - Correct battery RS485 port
  - Gateway TCP-to-RTU conversion mode
  - Other/concurrent Modbus clients
- Automatic register-map detection
  -Tries 34002 first for the existing V3-style map.
  - If unsupported/Illegal Data Address, automatically tries 32104.
- Correct SOC scaling
  - V3-style: 34002, raw × 0.1
  - Venus E V2: 32104, raw × 1.0
- Cycle Count handled gracefully
  - V3-style attempts 34003.
  - V2 does not attempt the known-invalid 34003; Cycle Count displays N/A




Changelog 1.2:
- Improved _read_holding():
  - Detects no response.
  - Detects Modbus exception responses (isError()).
  - Detects invalid responses that don't contain .registers.
  - Reports the register number in the error.
- Adds a communication self-test by reading register 30000 immediately after connecting.
- Logs gateway information at startup:
  - Gateway IP
  - TCP port
  - Slave ID
  - Poll interval
- Adds a clearer troubleshooting message when a Modbus error occurs, reminding users to check:
  - Slave ID
  - Baud rate (115200)
  - RS485 A/B wiring
  - DR134 "Modbus Simple Protocol Conversion" mode


Changelog 1.1:
- Supports pymodbus 2.x and 3.x
- Compatible with older Domoticz installations (e.g. 2025.1 on Raspberry P
- Logs the installed pymodbus version (when available)







