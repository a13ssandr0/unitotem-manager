#!/usr/bin/env bash
# Prepend a release entry to debian/changelog.
# Usage: set-release-version.sh VERSION [MESSAGE]
# Env: CHANGELOG (default: debian/changelog of this repository),
#      DEBFULLNAME, DEBEMAIL (entry author).
set -euo pipefail

die() { echo "error: $*" >&2; exit 1; }

[[ $# -ge 1 && $# -le 2 ]] || die "usage: $0 VERSION [MESSAGE]"

v=$1
msg=${2:-"Release $v"}

[[ $v != *$'\n'* ]] || die "VERSION contains a newline"
[[ $v =~ ^[0-9]{4}\.[0-9]{2}(\.[0-9]+)?(~[a-z]+[0-9]+)?$ ]] || die "invalid VERSION: $v"

[[ $msg != *$'\n'* ]] || die "MESSAGE contains a newline"

name=${DEBFULLNAME:-Alessandro Campolo}
email=${DEBEMAIL:-campolo.alex@gmail.com}
[[ $name != *$'\n'* && $email != *$'\n'* ]] || die "DEBFULLNAME or DEBEMAIL contains a newline"

root=$(cd "$(dirname "$0")/.." && pwd)
changelog=${CHANGELOG:-$root/debian/changelog}
[[ -f $changelog ]] || die "no changelog: $changelog"

first=""
IFS= read -r first < "$changelog" || true
re='^unitotem-manager \(([^()[:space:]]+)\) '
[[ $first =~ $re ]] || die "cannot read the top version from: $changelog"
top=${BASH_REMATCH[1]}

dpkg --compare-versions "$v" eq "$top" && die "VERSION $v equals the top version"
dpkg --compare-versions "$v" gt "$top" || die "VERSION $v is not greater than the top version $top"

dir=$(dirname -- "$changelog")
tmp=""
trap '[[ -z $tmp ]] || rm -f -- "$tmp"' EXIT
tmp=$(mktemp "$dir/.changelog.XXXXXX")
chmod --reference="$changelog" "$tmp"

{
    printf 'unitotem-manager (%s) stable; urgency=medium\n\n' "$v"
    printf '  * %s\n\n' "$msg"
    printf ' -- %s <%s>  %s\n\n' "$name" "$email" "$(date -R)"
    cat -- "$changelog"
} > "$tmp"

mv -f -- "$tmp" "$changelog"
tmp=""

printf 'unitotem-manager (%s) stable; urgency=medium\n' "$v"
