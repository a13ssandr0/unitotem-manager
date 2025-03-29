# class APT(WSAPIBase):
#     async def update(self, do_upgrade: bool = False):
#         global APT_THREAD
#         if not APT_THREAD.is_alive():
#             loop = asyncio.get_running_loop()
#             def apt():
#                 for line in apt_update(do_upgrade):
#                     if line == True:
#                         asyncio.run_coroutine_threadsafe(
#                               self.ws.broadcast('Settings/Update/start', upgrading=do_upgrade), loop)
#                     elif line == False:
#                         asyncio.run_coroutine_threadsafe(
#                               self.ws.broadcast('Settings/Update/end', upgrading=do_upgrade), loop)
#                     elif isinstance(line, tuple):
#                         asyncio.run_coroutine_threadsafe(
#                               self.ws.broadcast('Settings/Update/progress', upgrading=do_upgrade,
#                                           is_stdout=line[0], data=line[1]), loop)
#
#             APT_THREAD = Thread(target=apt, name=('upgrade' if do_upgrade else 'update'))
#             APT_THREAD.start()
#             return WSBroadcast('Settings/Update/status', status=APT_THREAD.name, log=get_apt_log())
#
#     async def list(self):
#         return WSBroadcast('Settings/Update/list', updates=apt_list_upgrades())
#
#     async def reboot_required(self):
#         return WSBroadcast('Settings/Update/reboot_required', reboot=reboot_required())
#
#     async def status(self):
#         return WSBroadcast('Settings/Update/status',
#                   status=APT_THREAD.name if APT_THREAD.is_alive() else None, log=get_apt_log())

