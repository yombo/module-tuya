#This file was created by Yombo for use with Yombo Gateway automation
#software. Details can be found at https://yombo.net
"""
Tuya
==============

Provides support for many WiFi devices that can be used by the Tuya/Jinvoo Android/ios app.

Before using this module to control these WiFi devices, you must use the Tuya app
to first configure your WiFi devices. Once complete, the device can be managed by Yombo.

.. note::

  These devices use a very low power WiFi module with a very small CPU. These devices
  can only have one connection at a time. Be sure that the Tuya and Tuya apps are not
  running in the background on your phone as this will prevent this module from sending
  commands to the WiFi devices.

.. todo::

  If anyone figures out how to interface with the Tuya MQTT servers, this will prevent
  connection conflicts and allow real-time status updates from the devices. Also, the
  devices will respond slightly faster to command requests.

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
try:  # Prefer simplejson if installed, otherwise json will work swell.
    import simplejson as json
except ImportError:
    import json
from time import time
from time import sleep as time_sleep
from socket import AF_INET, SOCK_STREAM, socket, SHUT_RDWR
from netaddr import IPNetwork

# Import twisted libraries
from twisted.internet import reactor
from twisted.internet.defer import inlineCallbacks, DeferredSemaphore, DeferredList
from twisted.internet import threads
from twisted.internet.task import LoopingCall

# Import 3rd party libraries
from yombo.ext.expiringdict import ExpiringDict

from yombo.core.exceptions import YomboWarning
from yombo.core.log import get_logger
from yombo.core.module import YomboModule
from yombo.utils.networking import get_local_network_info
from yombo.utils import sleep

from . import pytuya

from yombo.modules.jinvoo.web_routes import module_tuya_routes

logger = get_logger("modules.tuya")


class Jinvoo(YomboModule):
    """
    Adds support for Tuya controlled devices.
    """
    def _init_(self, **kwargs):
        """
        Setup a few basic items needed by this module.

        :param kwargs:
        :return:
        """
        self._module_starting()
        self.yombo_devices = self._module_devices_cached
        self.tuya_devices = {}  # Used to map tuya devices to yombo devices, and their IP address
        self.scan_running = False
        self.current_scan_results = {}
        self.status_cache = ExpiringDict(max_len=1000, max_age_seconds=5)
        self.scan_for_tuya_devices_loop = LoopingCall(self.scan_for_tuya_devices)

    @inlineCallbacks
    def _load_(self, **kwargs):
        yield self.scan_for_tuya_devices(fast=True, startup=True)
        self.scan_for_tuya_devices_loop.start(1800, False)
        reactor.callLater(30, self.scan_for_tuya_devices)

    def _device_changed_(self, **kwargs):
        """
        We listen for device updates, so we can re-scan when things change.

        :param kwargs:
        :return:
        """
        reactor.callLater(5, self.scan_for_tuya_devices)

    def _device_variables_updated_(self, **kwargs):
        """
        We listen for device variable updates, so we can re-scan when things change.

        :param kwargs:
        :return:
        """
        reactor.callLater(5, self.scan_for_tuya_devices)

    @inlineCallbacks
    def scan_for_tuya_devices(self, fast=None, startup=None):
        """
        Scours the local intranet for Tuya devices.

        :return:
        """
        if self.scan_running is True:
            return
        self.scan_running = True
        logger.debug("Tuya device scanning started.")
        self.current_scan_results = []
        if fast is True:
            number_of_workers = 30
        else:
            number_of_workers = 1
        address_info = get_local_network_info()
        iprange = IPNetwork(address_info['ipv4']['cidr'])

        search_semaphore = DeferredSemaphore(number_of_workers)
        all_searchers = []

        for host in iprange:
            d = search_semaphore.run(self.search_ip_address, str(host), 6668, fast)
            all_searchers.append(d)
        yield DeferredList(all_searchers)

        self.scan_running = False
        if startup is True:
            self._module_started()
        logger.debug("Tuya device scanning finished")

    @inlineCallbacks
    def search_ip_address(self, host, port, fast=None):
        """
        This method was split up to help enable semaphores.

        :param host:
        :param port:
        :return:
        """
        yield threads.deferToThread(self.do_search_ip_address, host, port, fast)

    def do_search_ip_address(self, host, port, fast=None):
        """
        Do the actual search. This is a blocking function.

        :param host:
        :param port:
        :return:
        """
        if fast is None:
            socket_timeout = .150
            device_timeout = .250
        else:
            socket_timeout = .3
            device_timeout = .400

        the_socket = socket(AF_INET, SOCK_STREAM)
        the_socket.settimeout(socket_timeout)
        try:
            the_socket.connect((host, port))
        except Exception:
            return
        try:
            the_socket.shutdown(SHUT_RDWR)
        except:
            pass
        try:
            the_socket.close()
        except:
            pass

        # we have a potential hit.
        for device_id, device in self._module_devices_cached.items():
            if device_id in self.current_scan_results:
                logger.debug("Device has already been matched, skipping. {label}", label=device.full_label)
                continue
            time_sleep(device_timeout)
            device_variables = device.device_variables_cached
            var_device_id = device_variables['device_id']['values'][0]
            if var_device_id == '':
                logger.warn("Device is missing Tuya device_id: {label}", label=device.full_label)
                continue
            var_local_key = device_variables['local_key']['values'][0]
            if var_local_key == '':
                logger.warn("Device is missing Tuya local_key: {label}", label=device.full_label)
                continue

            # logger.info("Testing (start): {host} {device} {key} ", host=host, device=var_device_id, key=var_local_key)
            try:
                tuya = pytuya.OutletDevice(var_device_id, host, var_local_key)
                data = tuya.status()
            except ConnectionResetError as e:
                logger.debug("Tuya connection reset error: {host}", host=host)
                continue
            except socket.timeout:
                logger.warn("Tuya refused connection, it appears the Tuya/Jinvoo app might be running:  {host}", host=host)
                continue
            if isinstance(data, dict) and 'dps' in data:
                self.current_scan_results.append(device_id)
                status = data['dps']
                device.tuya = tuya  # store reference to Tuya device for later.
                device.tuya_address = host
                device.tuya_id = var_device_id
                device.tuya_key = var_local_key
                if device.status != status:
                    self.set_device_status(device, status)
                return

    @inlineCallbacks
    def fetch_all_device_status(self, allow_cache=None):
        """
        Fetch the status of all known Tuya type devices. This simply iterates through the
        known list of devices this module manages using the magic variable "self._module_devices_cached".

        :param allow_cache:
        :return:
        """
        for device_id, device in self._module_devices_cached.items():
            self.fetch_device_status(device, allow_cache)
            yield sleep(0.100)

    @inlineCallbacks
    def fetch_device_status(self, device, allow_cache=None):
        """
        Fetch the status for a single Yombo device.

        :param device:
        :param allow_cache:
        :return:
        """
        start_time = time()
        received = False
        while received is False and time() - start_time < 5:
            try:
                status = yield self.fetch_remote_status(device, allow_cache)
                if device.status != status:
                    self.set_device_status(device, status)
                received = True
                return status
            except ConnectionResetError as e:
                logger.info("Unable to fetch remote status.")
                yield sleep(0.150)
        return None

    @inlineCallbacks
    def fetch_remote_status(self, device, allow_cache=None):
        """
        Fetch the status of a device. This will call do_fetch_remote_status() to get all the ports. It will then
        update the cache for all the ports on the device. This returns the status of a single port.

        This method uses threads to help ensure the system remain async.

        :param device:
        :param allow_cache:
        :return:
        """
        hash_id = device.device_id
        if allow_cache is not False and hash_id in self.status_cache:
            return self.status_cache[hash_id]

        results = yield threads.deferToThread(self.do_fetch_remote_status, device)
        for port_num, port_status in results.items():
            if isinstance(port_status, bool):
                self.status_cache[hash_id] = port_status
        return self.status_cache[hash_id]

    def do_fetch_remote_status(self, device):
        """
        This does the actual network fetch. It will return the status for every port for the current device.

        :param device:
        :return:
        """
        status = device.tuya.status()  # NOTE this does NOT require a valid key
        return status['dps']

    def set_device_status(self, device, status):
        """
        Sets the status.

        :param device:
        :param status:
        :return:
        """
        return
        if status == 0:
            command = self._Commands['off']
            status = 0
        else:
            command = self._Commands['on']
            status = 1

        device.set_status(machine_status=status,
                          command=command,
                          reported_by=self._FullName)

    @inlineCallbacks
    def send_network_command(self, device, status):
        """
        Set the device to reflect the desired status. True to turn on, false to turn off.

        :param deivce:
        :param status:
        :return:
        """
        start_time = time()
        received = False
        while received is False and time() - start_time < 5:
            try:
                received = yield threads.deferToThread(self.do_send_network_command, device, status)
                return received
            except ConnectionResetError as e:
                logger.info("Unable to to send_network_command: (reset error) {e}", e=e)
            except Exception as e:
                logger.info("Unable to to send_network_command: (other) {e}", e=e)
            yield sleep(0.15)
        return None

    def do_send_network_command(self, device, status):
        """
        This does the actual network operation.

        :param device:
        :param port:
        :return:
        """
        return device.tuya.set_status(status)

    @inlineCallbacks
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
        logger.debug("Got device command..for me")

        if hasattr(device, 'tuya') is False:
            logger.warn("Unable to control device: {label}, Tuya is missing from device.", label=device.full_label)
            return

        command = kwargs['command']
        command_label = command.machine_label
        if command_label == 'on':
            yield self.send_network_command(device, True)
        elif command_label == 'off':
            yield self.send_network_command(device, False)
        elif command_label == 'toggle':
            status = self.fetch_device_status(device, False)
            yield self.send_network_command(device, not status)
        device.device_command_done(request_id)

    # def _webinterface_add_routes_(self, **kwargs):
    #     """
    #     Currently, just a place holder for future Tuya module settings and tools.
    #
    #     :param kwargs:
    #     :return:
    #     """
    #     return {
    #         'nav_side': [
    #             {
    #                 'label1': 'Module Settings',
    #                 'label2': 'Tuya',
    #                 'priority1': 820,  # Even with a value, 'Tools' is already defined and will be ignored.
    #                 'priority2': 100,
    #                 'icon': 'fa fa-cog fa-fw',
    #                 'url': '/module_settings/tuya/index',
    #                 'tooltip': '',
    #                 'opmode': 'run',
    #             },
    #         ],
    #         'routes': [
    #             module_tuya_routes,
    #         ],
    #     }
