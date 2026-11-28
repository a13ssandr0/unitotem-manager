"""Restarts the device: through the updater when an update is armed, through logind otherwise."""


async def restart():
    from unitotem_node.system import updater
    if (await updater.status()).get('staged'):
        await updater.reboot()          # an armed update starts only with the updater's own restart
        return
    from unitotem_node.system.dbus_system import system_reboot
    await system_reboot()
