"""UniTotem 3.0.0 introduces configuration management through Pydantic models
Configuration file /etc/unitotem/unitotem.conf is now split in multiple files:
    - assets.json for assets list and playback options
    - users.json for users/groups
    - remote.json for client/server functions
"""

import json
import os


def advance():
    try:
        with open('/etc/unitotem/unitotem.conf') as file:
            config = json.load(file)
    except FileNotFoundError:
        return

    assets = []
    for asset in config.get('urls', []):
        asset['uuid'] = os.urandom(16).hex()
        assets.append(asset)

    with open('/etc/unitotem/assets.json', 'w') as file:
        json.dump({'assets': assets, 'default_duration': config.get('default_duration', 30)}, file, indent=4)

    users = {}
    for k,v in config.get('users', {}).items():
        # Versions prior to 3.0.0 didn't explicitly allow multiuser, but users were stored in a dictionary
        # to allow later implementation of multiuser capabilities
        # Old users were all admins, now it's necessary to specify that permission for each user being added to the new file
        users[k] = {
            "password": v['pass'],
            "permissions": ['admin'],
        }

    with open('/etc/unitotem/users.json', 'w') as file:
        json.dump(users, file, indent=4)

    os.replace('/etc/unitotem/unitotem.conf', '/etc/unitotem/unitotem.conf.old')