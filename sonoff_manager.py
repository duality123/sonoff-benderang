import asyncio
import logging
from typing import Optional

from aiohttp import ClientSession

# Make sure cloud.py and base.py are in your project and importable
from cloud import XRegistryCloud, XDevice

switches = {"one":"on","two":"on","three":"on","four":"on"}
_LOGGER = logging.getLogger(__name__)


class SonoffManager:
    """
    A convenient class that:
      1. Logs into eWeLink Cloud via XRegistryCloud.
      2. Starts the background WS (run_forever) for Cloud commands.
      3. Discovers switch-type devices (has 'switch' or 'switches' in params).
      4. Turns them ON/OFF using registry.send().
      5. Cleans up tasks upon close().
    """

    def __init__(self):
        self.session: Optional[ClientSession] = None
        self.registry: Optional[XRegistryCloud] = None
        self.devices: list[dict] = []  # store discovered devices here

    async def login(self, username: str, password: str, country_code: str = "+1") -> None:
        """
        1) Create the session + registry
        2) Log in to eWeLink Cloud
        3) Start the background WebSocket connection
        """
        _LOGGER.debug("Creating ClientSession...")
        self.session = ClientSession()

        _LOGGER.debug("Initializing XRegistryCloud...")
        self.registry = XRegistryCloud(session=self.session)

        # 1) Log into eWeLink Cloud
        #    If you have a token, call:
        #      await self.registry.login("token", "us:YOUR_TOKEN_HERE")
        #
        _LOGGER.debug("Logging in to eWeLink Cloud...")
        await self.registry.login(
            username=username,
            password=password,
            country_code=country_code
        )

        # 2) Start the registry's background task that opens and maintains
        #    the WebSocket to the eWeLink Cloud. This is critical for .send() calls.
        _LOGGER.debug("Starting eWeLink WebSocket connection (registry.run_forever).")
        self.registry.start()

        # Optionally wait a short time to let the WS fully connect.
        await asyncio.sleep(2)

    async def discover_switches(self) -> list[dict]:
        """
        Discover your eWeLink "homes," list all devices, and store only
        those that appear to be "switch-like."

        Returns:
            A list of discovered devices with "switch"/"switches" in their params.
        """
        if not self.registry:
            _LOGGER.error("Cannot discover devices; registry not initialized!")
            return []

        _LOGGER.debug("Fetching homes from eWeLink Cloud...")
        homes = await self.registry.get_homes()
        _LOGGER.debug(f"Homes found: {homes}")

        home_ids = list(homes.keys())  # collect all family IDs

        _LOGGER.debug("Fetching devices for discovered homes...")
        all_devices = await self.registry.get_devices(home_ids)
        _LOGGER.debug(f"All devices: {all_devices}")

        def is_switch(dev: dict) -> bool:
            params = dev.get("params", {})
            return ("switch" in params) or ("switches" in params)

        # Keep only devices that appear to be switches
        self.devices = [dev for dev in all_devices if is_switch(dev)]
        return self.devices

    async def turn_on(self, device_id: str, num: str) -> str:
            """
            Turn ON a specific channel/outlet of a device.
            """
            if not self.registry:
                return "E#NoRegistry"

            device = self._find_device(device_id)
            if not device:
                return f"E#DeviceNotFound:{device_id}"

            outlet_idx = int(num)
            
            # Ambil daftar switches saat ini dari device, atau buat default 4 channel jika belum ada
            current_switches = device.get("params", {}).get("switches", [
                {"outlet": 0, "switch": "off"},
                {"outlet": 1, "switch": "off"},
                {"outlet": 2, "switch": "off"},
                {"outlet": 3, "switch": "off"}
            ])

            # Pastikan formatnya memuat 'outlet' dan 'switch'
            updated_switches = []
            for ch in current_switches:
                outlet_id = ch.get("outlet", 0)
                status = "on" if outlet_id == outlet_idx else ch.get("switch", "off")
                updated_switches.append({"outlet": outlet_id, "switch": status})

            payload = {"switches": updated_switches}

            _LOGGER.debug(f"Turning ON: {device.get('name')} (deviceid={device_id}) outlet {outlet_idx}")
            return await self.registry.send(device, payload)

    async def turn_off(self, device_id: str, num: str) -> str:
        """
        Turn OFF a specific channel/outlet of a device.
        """
        if not self.registry:
            return "E#NoRegistry"

        device = self._find_device(device_id)
        if not device:
            return f"E#DeviceNotFound:{device_id}"

        outlet_idx = int(num)

        current_switches = device.get("params", {}).get("switches", [
            {"outlet": 0, "switch": "off"},
            {"outlet": 1, "switch": "off"},
            {"outlet": 2, "switch": "off"},
            {"outlet": 3, "switch": "off"}
        ])

        updated_switches = []
        for ch in current_switches:
            outlet_id = ch.get("outlet", 0)
            status = "off" if outlet_id == outlet_idx else ch.get("switch", "off")
            updated_switches.append({"outlet": outlet_id, "switch": status})

        payload = {"switches": updated_switches}

        _LOGGER.debug(f"Turning OFF: {device.get('name')} (deviceid={device_id}) outlet {outlet_idx}")
        return await self.registry.send(device, payload)

    # ---------------------------------------------------------------------
    # Helpers
    # ---------------------------------------------------------------------
    def _find_device(self, device_id: str) -> Optional[XDevice]:
        """Utility to locate a device by deviceid in self.devices."""
        return next((d for d in self.devices if d["deviceid"] == device_id), None)