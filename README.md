# UniTotem-manager

Backend manager for [UniTotem](https://github.com/a13ssandr0/unitotem)

## IMPORTANT
When upgrading from versions before 2.2.1 you need to reimport the gpg key with:
```sh
sudo curl -s --compressed -o /etc/apt/trusted.gpg.d/unitotem-manager.asc "https://a13ssandr0.github.io/unitotem-manager/KEY.gpg"
```

## Installing this repository
```sh
sudo curl -s --compressed -o /etc/apt/trusted.gpg.d/unitotem-manager.asc "https://a13ssandr0.github.io/unitotem-manager/KEY.gpg"
sudo curl -s --compressed -o /etc/apt/sources.list.d/unitotem-manager.list "https://a13ssandr0.github.io/unitotem-manager/unitotem-manager.list"
sudo apt update
sudo apt install unitotem-manager
```

## Known bugs
None

## TODO
- No changes programmed

