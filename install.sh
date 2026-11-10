#!/bin/sh
set -eu

repository=https://github.com/nativmedia/CLIPY.ID-Client.git
case "$(uname -s)" in
  Darwin) destination="$HOME/Library/Application Support/CLIPY.ID-Client" ;;
  Linux) destination="${XDG_DATA_HOME:-$HOME/.local/share}/CLIPY.ID-Client" ;;
  *) printf '%s\n' 'Gunakan install.ps1 untuk Windows.' >&2; exit 1 ;;
esac

for command in git npm cargo; do
  if ! command -v "$command" >/dev/null 2>&1; then
    printf '%s\n' "$command belum tersedia. Periksa prasyarat di README." >&2
    exit 1
  fi
done

if [ ! -e "$destination" ]; then
  mkdir -p "$(dirname "$destination")"
  git clone -- "$repository" "$destination"
else
  if [ "$(git -C "$destination" remote get-url origin)" != "$repository" ]; then
    printf '%s\n' "Folder $destination sudah digunakan. Gunakan git clone untuk memilih folder lain." >&2
    exit 1
  fi
  git -C "$destination" pull --ff-only
fi

cd "$destination"
npm ci
npm run setup
exec npm run dev:desktop
