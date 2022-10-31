#!/bin/bash
cd "$(dirname ${BASH_SOURCE[0]})/.."

dpkg-buildpackage -A
rsync -v ../unitotem-manager_*.deb docs/

cd "$(dirname ${BASH_SOURCE[0]})"

dpkg-scanpackages --multiversion . > Packages
gzip -k -f Packages
apt-ftparchive release . > Release
gpg --default-key "campoloalex@gmail.com" -abs -o - Release > Release.gpg
gpg --default-key "campoloalex@gmail.com" --clearsign -o - Release > InRelease