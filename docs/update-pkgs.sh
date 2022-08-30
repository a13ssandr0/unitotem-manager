#!/bin/bash

dpkg-scanpackages --multiversion . > Packages
gzip -k -f Packages
apt-ftparchive release . > Release
gpg --default-key "campoloalex@gmail.com" -abs -o - Release > Release.gpg
gpg --default-key "campoloalex@gmail.com" --clearsign -o - Release > InRelease