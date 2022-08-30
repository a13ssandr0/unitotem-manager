# UniTotem-manager

Backend manager for [UniTotem](https://github.com/a13ssandr0/unitotem)

## Installing this repository
```sh
wget -qO- "https://a13ssandr0.github.io/unitotem-manager/KEY.gpg" | sudo tee /etc/apt/trusted.gpg.d/unitotem-manager.asc
sudo curl -s --compressed -o /etc/apt/sources.list.d/unitotem-manager.list "https://a13ssandr0.github.io/unitotem-manager/my_list_file.list"
sudo apt update
sudo apt install unitotem-manager
```