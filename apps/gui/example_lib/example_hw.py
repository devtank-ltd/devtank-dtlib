import os
import time
import random
import dt_db_base

from .check_descs import CHECK_DESCS
from dt_db_base import base_hw_dev, base_hw_bus, base_hw_bus_con

## Example device
class example_dev(base_hw_dev):
    def __init__(self, uuid):
        self._uuid = uuid
        self.test_check      = None
        self.threshold_check = None
        self.exact_check     = None
        self.store_value     = None
        self._fw = ""
        self._write_enable = False
        self._hw_id = "%02x:%02x:%02x:%02x:%02x:%02x" % \
                    (random.randint(0, 255),
                     random.randint(0, 255),
                     random.randint(0, 255),
                     random.randint(0, 255),
                     random.randint(0, 255),
                     random.randint(0, 255))

    def set_test_functions(self, test_check, threshold_check, exact_check, store_value):
        self.threshold_check = threshold_check
        self.exact_check     = exact_check
        self.store_value     = store_value
        self.test_check      = test_check

    def update_uuid_from_hw(self):
        if not self._fw:
            dt_db_base.error_msg("HW ID can not be read without firmware.")
            r = False
        else:
            dt_db_base.info_msg(f'CARD1 : UART0 << "GET_HW_ID"')
            dt_db_base.info_msg(f'CARD1 : UART0 >> "HW ID: {self._hw_id}"')
            self._uuid = self._hw_id
            r = True
        self.exact_check(r, True, CHECK_DESCS.FIRMWARE_HW_ID)

    def read_serial(self):
        r = self._fw
        dt_db_base.info_msg(f'CARD1 : UART0 >> "{r}"')
        return r

    def _fake_adc(self, name, unit, cal, readings):
        unit_fn = lambda v : round(cal[0] + v * cal[1])
        min_v = min(readings)
        max_v = max(readings)
        avg_v = sum(readings) / len(readings)
        avg_unit = unit_fn(avg_v)
        dt_db_base.info_msg(f'CARD1 : ADC "{name}" cal {cal}')
        dt_db_base.info_msg(f'CARD1 : ADC "{name}" {len(readings)} samples')
        dt_db_base.info_msg(f'CARD1 : ADC "{name}" min {min_v}/{unit_fn(min_v)}{unit}')
        dt_db_base.info_msg(f'CARD1 : ADC "{name}" max {max_v}/{unit_fn(max_v)}{unit}')
        dt_db_base.info_msg(f'CARD1 : ADC "{name}" avg {avg_v}/{avg_unit}{unit}')
        return avg_unit

    def read_3v3_rail(self):
        cal = (0, round(5000 / 4095, 3)) # 5V over 12bit ADC, 0 offset
        readings = [random.randint(2700, 2704) for _ in range(1000)]
        mV = self._fake_adc("DUT_3V3", "mv", cal, readings)
        return mV

    def read_current(self):
        adc = 289
        cal = (0, round(2000 / 4095, 3)) # 2A over 12bit ADC, 0 offset
        readings = [random.randint(285, 295) for _ in range(1000)]
        mA = self._fake_adc("DUT_CUR", "mA", cal, readings)
        return mA

    def read_revision(self):
        dt_db_base.info_msg('CARD1 : IO REV_GPIO_0')
        dt_db_base.info_msg('CARD1 : IO 5 "REV_GPIO_0" = 1')
        dt_db_base.info_msg('CARD1 : IO REV_GPIO_1')
        dt_db_base.info_msg('CARD1 : IO 6 "REV_GPIO_1" = 0')
        dt_db_base.info_msg('CARD1 : IO REV_GPIO_2')
        dt_db_base.info_msg('CARD1 : IO 7 "REV_GPIO_2" = 1')
        return 101

    def send_firmware(self, fw):
        dt_db_base.info_msg("Uploading firmware")
        with open(fw) as f:
            self._fw = f.readline().strip()
        dt_db_base.info_msg("CARD1 : IO BOOT_GPIO = 1")
        self.reset()
        for n in range(5):
            dt_db_base.info_msg("CARD1 : UART0 << XXXXXXXXXXXXXXXX")
            time.sleep(0.1)
        dt_db_base.info_msg("CARD1 : IO BOOT_GPIO = 0")
        dt_db_base.info_msg("Firmware loaded")
        return True

    def reset(self):
        dt_db_base.info_msg("CARD1 : IO RESET_GPIO = 0")
        time.sleep(0.1)
        dt_db_base.info_msg("CARD1 : IO RESET_GPIO = 1")


## Open connection to a Example bus.
class example_bus_con(base_hw_bus_con):
    def __init__(self):
        super().__init__()

    ## Load in known device UUIDs
    def ready_devices(self, known_devices):
        if len(known_devices):
            self._devices = [ example_dev(known_devices[0].uuid) ]
        else:
            self._devices = []

## Example bus ready to be openned for use.
class example_bus(base_hw_bus):
    def __init__(self):
        super().__init__()

    def open(self):
        dt_db_base.info_msg("Openning my bus")
        self._obj = example_bus_con()
        return self._obj

    def close(self):
        super().close()
        dt_db_base.info_msg("Closing my bus")
