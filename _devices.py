"""
This file is used by the Yombo core to create a device object for the specific zwave devices.
"""
from yombo.lib.devices.switch import Switch


class Jinvoo_Switch(Switch):
    """
    Simple jinvoo based switch
    """
    SUB_PLATFORM = 'jinvoo'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.FEATURES.update({
            'number_of_steps': 1,
            'sends_updates': True,
            }
        )
