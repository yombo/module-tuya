#This file was created by Yombo for use with Yombo Gateway automation
#software. Details can be found at https://yombo.net
"""
Jinvoo
==============

Provides support for any type of WiFi that uses the Jinvoo app.

Before using this module to control these WiFi devices, you must use the Jinvoo app
to first configure your WiFi devices. Once complete, the device can be managed by Yombo.

License
=======

See LICENSE.md for full license and attribution information.

The Yombo team and other contributors hopes that it will be useful, but
WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY
or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for
more details.

.. moduleauthor:: Mitch Schwenk <mitch-gw@yombo.net>
:license: Apache 2.0
"""
# Import python libraries
from collections import OrderedDict, deque
from pydispatch import dispatcher
try:  # Prefer simplejson if installed, otherwise json will work swell.
    import simplejson as json
except ImportError:
    import json
import pytuya

# Import twisted libraries
from twisted.internet.defer import inlineCallbacks
from twisted.internet import threads
from twisted.internet.task import LoopingCall
from hashlib import blake2s

# Import 3rd party libraries
from yombo.ext.expiringdict import ExpiringDict

from yombo.core.exceptions import YomboWarning
from yombo.utils import sleep
from yombo.utils.decorators import memoize_ttl
from yombo.core.log import get_logger
from yombo.core.module import YomboModule
#from yombo.lib.webinterface.auth import require_auth

from . import const

from yombo.modules.zwave.web_routes import module_zwave_routes

logger = get_logger("modules.jinvoo")


class Jinvoo(YomboModule):
    """
    Adds support for Jinvoo controlled devices.
    """
    def _obj_to_dict(self, obj):
        """
        Convert an object into a hash for debug.
        """
        return {key: getattr(obj, key) for key
                in dir(obj)
                if key[0] != '_' and not hasattr(getattr(obj, key), '__call__')}

    def _init_(self, **kwargs):
        self.cache = ExpiringDict(max_len=1000, max_age_seconds=1)
        self.poll_status_loop = LoopingCall(self.fetch_all_device_status, cache=False)

    def _load_(self, **kwargs):
        self.poll_status_loop.start(2)

    @inlineCallbacks
    def fetch_all_device_status(self, cache=None):
        print("starting fetch_all_device_status")
        for device_id, device in self._module_devices_cached.items():
            print("feting device: %s" % device.full_label)
            self.fetch_device_status(device, cache)
            yield sleep(0.01)

    @inlineCallbacks
    def fetch_device_status(self, device, cache=None):
        device_variables = device.device_variables_cached

        var_device_id = device_variables['device_id']['values'][0]
        if var_device_id == '':
            raise YomboWarning("Jinvoo cannot fetch device status, missing device_id")
        var_ip_address = device_variables['ip_address']['values'][0]
        if var_ip_address == '':
            raise YomboWarning("Jinvoo cannot fetch device status, missing ip_address")
        var_local_key = device_variables['local_key']['values'][0]
        if var_local_key == '':
            raise YomboWarning("Jinvoo cannot fetch device status, missing local_key")
        var_port = device_variables['port']['values'][0]
        if var_port == '':
            raise YomboWarning("Jinvoo cannot fetch device status, missing port")

        status = self.fetch_status(var_device_id, var_ip_address, var_local_key, var_port, cache)
        if device.status != status:
            self.set_device_status(device, status)

    def set_device_status(self, device, status, ):
        if status == 0:
            command = self._Commands['off']
        else:
            command = self._Commands['on']

        device.set_status(machine_status=status,
                          command=command,
                          reported_by=self._FullName)

    @inlineCallbacks
    def fetch_status(self, device_id, ip_address, local_key, port=1, cache=None):
        """
        Fetch the status of a device. This will call do_fetch_status() to get all the ports. It will then
        update the cache for all the ports on the device. This returns the status of a single port.

        :param device_id:
        :param ip_address:
        :param local_key:
        :param port:
        :param cache:
        :return:
        """
        hash_id = self.hash_id(device_id, ip_address, local_key, port)
        if cache is not False and hash_id in self.cache:
            return self.cache[hash_id]

        results = yield threads.deferToThread(self.do_fetch_status, device_id, ip_address, local_key)
        for port_num, port_status in results.items():
            if isinstance(port_status, bool):
                id = self.hash_id(device_id, ip_address, local_key, port_num)
                self.cache[id] = port_status
        return self.cache[hash_id]

    def do_fetch_status(self, device_id, ip_address, local_key):
        """
        This does the actual network fetch. It will return the status for every port for the current device.

        :param device_id:
        :param ip_address:
        :param local_key:
        :param port:
        :return:
        """
        d = pytuya.OutletDevice(device_id, ip_address, local_key)
        data = d.status()  # NOTE this does NOT require a valid key
        print('Dictionary %r' % data)
        print('state (bool, true is ON) %r' % data['dps']['1'])  # Show status of first controlled switch on device
        return data['dps']

    def hash_id(self, device_id, ip_address, local_key, port):
        return blake2s(
                    str("%s:%s:%s:%s" % (device_id, ip_address, local_key, port)).encode()
                ).hexdigest()

    # @inlineCallbacks
    def _device_command_(self, **kwargs):
        """
        Received a request to do perform a command for a piface digital output interface.

        :param kwags: Contains 'device' and 'command'.
        :return: None
        """
        device = kwargs['device']
        if self._is_my_device(device) is False:
            return  # not meant for us.
        request_id = kwargs['request_id']

        # print("device command starting....looking for device_variables_cached")
        device_variables = device.device_variables_cached

        device_home_id = device_variables['home_id']['values'][0]
        device_node_id = device_variables['node_id']['values'][0]
        if self.ready is False:
            addr = self.encode_node_id(device_home_id, device_node_id)
            if addr in self.device_command_queue:
                device.device_command_failed(request_id, message='Command superseded by another.')
            self.device_command_queue[addr] = kwargs
            device.device_command_pending(request_id, message=_('module.zwave', 'ZWave module still starting, command will be processed when fully loaded.'))
            return

        if device_home_id not in self.nodes:
            logger.warn("Yombo reports a zwave home_id for a device, but home_id missing. Device: %s" % device.area_label)
            device.device_command_failed(request_id,
                                         message=_('module.zwave', 'Device is missing valid home_id'))
            return
        if device_node_id not in self.nodes[device_home_id]:
            logger.warn("Yombo reports a zwave node_id for a device, but node_id missing. Device: %s" % device.area_label)
            device.device_command_failed(request_id,
                                         message=_('module.zwave', 'Device is missing valid node_id'))
            return

        zwave_device = self.nodes[device_home_id][device_node_id]

        device.device_command_processing(request_id, message=_('module.zwave', 'Device command being processed by ZWave module.'))

        results = zwave_device.do_command(**kwargs)
        if results[0] == 'failed':
            device.device_command_failed(request_id, message=results[1])
        elif results[0] == 'done':
            device.device_command_done(request_id, message=results[1])
        else:
            device.device_command_done(request_id)

    def _webinterface_add_routes_(self, **kwargs):
        """
        Adds a configuration block to the web interface. This allows users to view their
        zwave devices. and quickly add a new zwave device.

        :param kwargs:
        :return:
        """
        return {
            'nav_side': [
                {
                    'label1': 'Module Settings',
                    'label2': 'Jinvoo',
                    'priority1': 820,  # Even with a value, 'Tools' is already defined and will be ignored.
                    'priority2': 100,
                    'icon': 'fa fa-cog fa-fw',
                    'url': '/module_settings/jinvoo/index',
                    'tooltip': '',
                    'opmode': 'run',
                },
            ],
            'routes': [
                module_zwave_routes,
            ],
        }
