"""
<plugin key="Marstek_modbus"
        name="Marstek Venus Modbus"
        author="Simon Riemersma"
        version="1.3.1">

    <params>
        <param field="Address" label="Gateway IP Address" width="200px" required="true"/>
        <param field="Port" label="TCP Port" width="60px" default="502"/>
        <param field="Mode1" label="Modbus Slave ID" width="60px" default="1"/>
        <param field="Mode6" label="Poll interval (seconds)" width="75px" default="30"/>
    </params>
</plugin>
"""

import Domoticz
from pymodbus.client import ModbusTcpClient

MODE_NAMES = {0:"Manual",1:"Anti-feed",2:"Trade"}
MODE_LEVELS = {0:0,1:10,2:20}
LEVEL_TO_MODE = {0:0,10:1,20:2}

class BasePlugin:

    def __init__(self):
        self.counter = 0
        self.register_profile = None
        # 33004/33006 are daily-reset counters on the Marstek side (no lifetime/
        # cumulative register exists in this plugin's register map). Domoticz's
        # kWh device type expects an ever-increasing total so it can compute
        # "Today" itself, so we accumulate a running lifetime total in Wh here.
        self.prev_daily_charge = None
        self.prev_daily_discharge = None
        self.total_charge_wh = None
        self.total_discharge_wh = None

    def onStart(self):

        # One-time migration: units 22/23 were previously created as Custom Sensor
        # (Type=243/Subtype=31), which Domoticz shows as plain text, not a real kWh
        # meter. Recreate them as Subtype=29 ("kWh") so dashboards can use them as
        # proper energy devices. This drops history for these two units only.
        for unit in (22, 23):
            if unit in Devices and Devices[unit].SubType != 29:
                Domoticz.Log("Marstek Modbus: recreating Unit {} as kWh energy meter (was Custom Sensor)".format(unit))
                Devices[unit].Delete()

        defs = [
            (1,"SOC","Percentage"),
            (2,"Remaining Energy","kWh"),
            (3,"Battery Voltage","Voltage"),
            (4,"Battery Current","CustomA"),
            (5,"Battery Power","Usage"),
            (6,"Battery Temperature","Temperature"),
            (7,"Current Mode","Text"),
            (8,"Mode Selector","Selector"),
            (9,"Battery Capacity","kWh"),
            (10,"AC Power","Usage"),
            (11,"Connection Status","Text"),
            (12,"RS485 Control Status","Text"),
            (13,"Cycle Count","Custom"),
            (14,"Battery Efficiency","Percentage"),
            (15,"Charge/Discharge Direction","Text"),
            (16,"Estimated SOH","Percentage"),
            (17,"Internal Temperature","Temperature"),
            (18,"MOS1 Temperature","Temperature"),
            (19,"MOS2 Temperature","Temperature"),
            (20,"Max Cell Voltage","Voltage"),
            (21,"Min Cell Voltage","Voltage"),
            (22,"Daily Charge Energy","EnergyCounter"),
            (23,"Daily Discharge Energy","EnergyCounter"),
        ]

        for unit,name,typ in defs:
            if unit in Devices:
                continue

            if typ == "Text":
                Domoticz.Device(Name=name,Unit=unit,Type=243,Subtype=19).Create()
            elif typ == "Selector":
                Domoticz.Device(Name=name,Unit=unit,TypeName="Selector Switch",
                                Options={"LevelActions":"|||",
                                         "LevelNames":"Manual|Anti-feed|Trade",
                                         "LevelOffHidden":"false",
                                         "SelectorStyle":"0"}).Create()
            elif typ == "CustomA":
                Domoticz.Device(Name=name,Unit=unit,Type=243,Subtype=31,
                                Options={"Custom":"A"}).Create()
            elif typ == "Custom":
                Domoticz.Device(Name=name,Unit=unit,Type=243,Subtype=31).Create()
            elif typ == "kWh":
                Domoticz.Device(Name=name,Unit=unit,Type=243,Subtype=31,
                                Options={"Custom":"kWh"}).Create()
            elif typ == "EnergyCounter":
                Domoticz.Device(Name=name,Unit=unit,Type=243,Subtype=29).Create()
            elif typ == "Percentage":
                Domoticz.Device(Name=name,Unit=unit,TypeName="Percentage").Create()
            else:
                Domoticz.Device(Name=name,Unit=unit,TypeName=typ).Create()

        # Seed the running lifetime totals from whatever Domoticz already has
        # persisted for these devices (Domoticz keeps the last sValue across
        # plugin restarts), so we don't reset the counter to 0 on every restart.
        self.total_charge_wh = self._seed_total_wh(22)
        self.total_discharge_wh = self._seed_total_wh(23)

        Domoticz.Heartbeat(10)

    def _seed_total_wh(self,unit):
        if unit in Devices:
            try:
                parts=Devices[unit].sValue.split(";")
                if len(parts) == 2:
                    return float(parts[1])
            except Exception:
                pass
        return 0.0

    def _accumulate_wh(self,new_daily_kwh,prev_daily_kwh,running_total_wh):
        if prev_daily_kwh is None:
            # First poll after a restart: don't know how much of new_daily_kwh
            # is already reflected in the seeded running total, so contribute
            # nothing this cycle and just establish the baseline.
            delta_kwh=0.0
        elif new_daily_kwh < prev_daily_kwh:
            # Daily counter reset at midnight (or device reboot); the new,
            # smaller value IS the delta accrued since the reset.
            delta_kwh=new_daily_kwh
        else:
            delta_kwh=new_daily_kwh - prev_daily_kwh
        return running_total_wh + delta_kwh*1000.0

    def client(self):
        return ModbusTcpClient(Parameters["Address"], port=int(Parameters["Port"]))

    def read_u16(self,c,r):
        return c.read_holding_registers(address=r,count=1,device_id=int(Parameters["Mode1"])).registers[0]

    def read_s16(self,c,r):
        v=self.read_u16(c,r)
        return v-65536 if v>32767 else v

    def read_u32(self,c,r):
        rr=c.read_holding_registers(address=r,count=2,device_id=int(Parameters["Mode1"]))
        return (rr.registers[0] << 16) | rr.registers[1]

    def _is_illegal_address_error(self,e):
        t=str(e).lower()
        return ("exception_code=2" in t or "code=2" in t or
                "illegal data address" in t)

    def _detect_register_profile(self,c):
        # Only fall back to V2 after an explicit Illegal Data Address (02).
        # A timeout/ModbusIOException is a communication fault, not model detection.
        try:
            raw=self.read_u16(c,34002)
            if not 0 <= raw <= 1000:
                raise Exception("Implausible SOC raw value {} at 34002".format(raw))
            self.register_profile={"name":"V3-style","soc_register":34002,
                                   "soc_scale":0.1,"cycle_register":34003}
            Domoticz.Log("Marstek Modbus: detected V3-style map (SOC 34002 x0.1)")
            return
        except Exception as e:
            if not self._is_illegal_address_error(e):
                raise Exception(
                    "Register-map detection stopped: 34002 did not return a valid value "
                    "or explicit Illegal Data Address (02). Treating this as a communication "
                    "problem, not as Venus E V2. Details: {}".format(e))
            Domoticz.Log("Marstek Modbus: 34002 returned Illegal Data Address (02); trying V2 SOC 32104.")

        try:
            raw=self.read_u16(c,32104)
        except Exception as e:
            raise Exception(
                "Possible Venus E V2 (34002 returned exception 02), but 32104 did not "
                "return a valid SOC response. Model not selected. Details: {}".format(e))
        if not 0 <= raw <= 100:
            raise Exception("V2 SOC 32104 returned {}, expected 0..100".format(raw))
        self.register_profile={"name":"Venus E V2","soc_register":32104,
                               "soc_scale":1.0,"cycle_register":None}
        Domoticz.Log("Marstek Modbus: detected Venus E V2 map (SOC 32104 x1.0; cycle count unavailable)")

    def _ensure_register_profile(self,c):
        if self.register_profile is None:
            self._detect_register_profile(c)
        return self.register_profile

    def _read_cycle_count(self,c):
        p=self._ensure_register_profile(c)
        reg=p.get("cycle_register")
        if reg is None:
            return None
        try:
            return self.read_u16(c,reg)
        except Exception as e:
            Domoticz.Log("Marstek Modbus: Cycle Count unavailable: {}".format(e))
            p["cycle_register"]=None
            return None

    def onCommand(self, Unit, Command, Level, Hue):
        if Unit != 8:
            return
        c=self.client()
        if not c.connect():
            return
        try:
            c.write_register(address=43000,value=LEVEL_TO_MODE.get(Level,0),
                             device_id=int(Parameters["Mode1"]))
        finally:
            c.close()

    def onHeartbeat(self):
        self.counter += 10
        interval=max(10,int(Parameters["Mode6"] or 30))
        if self.counter < interval:
            return
        self.counter=0

        c=self.client()
        if not c.connect():
            Devices[11].Update(0,"Disconnected")
            return

        try:
            Devices[11].Update(0,"Connected")

            profile=self._ensure_register_profile(c)
            Devices[11].Update(0,"Connected ({})".format(profile["name"]))
            soc=self.read_u16(c,profile["soc_register"])*profile["soc_scale"]
            capacity=self.read_u16(c,32105)*0.001
            remaining=capacity*soc/100.0

            voltage=self.read_u16(c,30100)/100.0
            current=self.read_s16(c,30101)/10.0
            battery_power=self.read_s16(c,30001)

            temp=self.read_s16(c,35000)/10.0
            mos1=self.read_s16(c,35001)/10.0
            mos2=self.read_s16(c,35002)/10.0

            mode=self.read_u16(c,43000)
            ac_power=self.read_s16(c,30006)

            rs485=self.read_u16(c,42000)
            cycle_count=self._read_cycle_count(c)

            max_cell=self.read_u16(c,37007)/1000.0
            min_cell=self.read_u16(c,37008)/1000.0

            daily_charge=self.read_u32(c,33004)*0.01
            daily_discharge=self.read_u32(c,33006)*0.01

            eff=round(abs(ac_power)/abs(battery_power)*100,1) if abs(battery_power)>50 else 0

            # inverted as requested
            if battery_power < -50:
                direction="Discharging"
            elif battery_power > 50:
                direction="Charging"
            else:
                direction="Idle"

            rs485_status="Enabled" if rs485==21930 else ("Disabled" if rs485==21947 else str(rs485))

            soh=100.0

            Devices[1].Update(0,str(round(soc,1)))
            Devices[2].Update(0,str(round(remaining,3)))
            Devices[3].Update(0,str(round(voltage,2)))
            Devices[4].Update(0,str(round(current,1)))
            Devices[5].Update(0,str(battery_power))
            Devices[6].Update(0,str(round(temp,1)))
            Devices[7].Update(0,MODE_NAMES.get(mode,str(mode)))
            Devices[8].Update(nValue=1,sValue=str(MODE_LEVELS.get(mode,0)))
            Devices[9].Update(0,str(round(capacity,3)))
            Devices[10].Update(0,str(ac_power))
            Devices[12].Update(0,rs485_status)
            if cycle_count is not None:
                Devices[13].Update(0,str(cycle_count))
            elif Devices[13].sValue != "N/A":
                Devices[13].Update(0,"N/A")
            Devices[14].Update(0,str(eff))
            Devices[15].Update(0,direction)
            Devices[16].Update(0,str(soh))
            Devices[17].Update(0,str(round(temp,1)))
            Devices[18].Update(0,str(round(mos1,1)))
            Devices[19].Update(0,str(round(mos2,1)))
            Devices[20].Update(0,str(round(max_cell,3)))
            Devices[21].Update(0,str(round(min_cell,3)))
            # Subtype 29 (kWh) needs "power;energy_Wh" where energy_Wh must be an
            # ever-increasing lifetime total for Domoticz's own Today-diff to work.
            # 33004/33006 are daily-reset registers, so accumulate a running total
            # here instead of passing the raw daily value through.
            self.total_charge_wh=self._accumulate_wh(daily_charge,self.prev_daily_charge,self.total_charge_wh)
            self.prev_daily_charge=daily_charge
            self.total_discharge_wh=self._accumulate_wh(daily_discharge,self.prev_daily_discharge,self.total_discharge_wh)
            self.prev_daily_discharge=daily_discharge

            # Live power for the power field: deliberately battery_power (30001),
            # not ac_power (30006). battery_power already has an established,
            # in-code-verified sign convention (used for `direction` above:
            # >50 Charging, <-50 Discharging); ac_power's polarity relative to
            # charge/discharge is undocumented here and only ever used as a
            # magnitude (abs()) elsewhere, so it isn't safe to trust its sign.
            # Gating on `direction` keeps charge/discharge mutually exclusive:
            # only the tile matching the current state shows a nonzero Watt
            # value, the other reports 0, matching a battery that can't charge
            # and discharge at the same time.
            charge_power=battery_power if direction == "Charging" else 0
            discharge_power=abs(battery_power) if direction == "Discharging" else 0

            Devices[22].Update(nValue=0,sValue="{};{}".format(charge_power,round(self.total_charge_wh)))
            Devices[23].Update(nValue=0,sValue="{};{}".format(discharge_power,round(self.total_discharge_wh)))
        except Exception as e:
            Domoticz.Error("Marstek Modbus: {}".format(e))
        finally:
            c.close()

global _plugin
_plugin=BasePlugin()

def onStart(): _plugin.onStart()
def onHeartbeat(): _plugin.onHeartbeat()
def onCommand(Unit, Command, Level, Hue): _plugin.onCommand(Unit, Command, Level, Hue)
