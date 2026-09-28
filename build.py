#!/usr/bin/env python3
"""Build the merged Project Wingman mod pak from your own game install and the original mods.

    python3 build.py --mods <folder with the 3 original downloads> [--game <install dir>]
                     [--out <file.pak>] [--install] [--plain]

Nothing from the game or the original mods is shipped with this tool: it reads the data
tables from your install, takes only the changes each mod makes, and writes a new pak.
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pwmod.pak import Pak, write_pak          # noqa: E402
from pwmod.datatable import DataTable         # noqa: E402

OUT_NAME = '~~Merged_WSO_AoA_Unreleased_P.pak'
BASE_PAK = 'ProjectWingman/Content/Paks/pakchunk0-WindowsNoEditor.pak'

MAIN = 'ProjectWingman/Content/ProjectWingman/Blueprints/Data/AircraftData/DB_Aircraft'
MF = 'ProjectWingman/Plugins/MagadanFront/Content/MagadanFront/Blueprints/Data/DB_MF_Aircraft'
WEAP = 'ProjectWingman/Content/ProjectWingman/Blueprints/Data/Weapons/DWeaponDB'

MODS = {
    'wso': ('More WSOs', lambda n: 'morewsos' in n),
    'aoa': ('AoA for All and Mk2 planes for Campaign (F59)', lambda n: 'aoa for all' in n),
    'unrel_base': ('Playable Unreleased Aircraft 2.1 (Base)',
                   lambda n: 'playable unreleased aircraft' in n and '(base)' in n),
    'unrel_weap': ('Playable Unreleased Aircraft 2.1 (Weapons)',
                   lambda n: 'playable unreleased aircraft' in n and '(weapons)' in n),
}

# Playable Unreleased Planes was made for a 2021 build. Its tables are older than the game's
# (4 aircraft missing, fields added since), so only its intended changes are taken:
UNREL_AIRCRAFT_FIELDS = {'.ID.Available', '.ID.Unlocked', '.ID.Purchased', '.Price',
                         '.CQ_ID.CQ_Available', '.CQ_ID.CQ_Unlocked', '.CQ_ID.CQ_Purchased',
                         '.CQ_ID.CQ_Price'}
UNREL_WEAPON_FIELDS = {('EUBM', '.IsAvailable'), ('BMLAA', '.IsAvailable'),
                       ('RDBM', '.CQOnly'), ('EUFB', '.CQOnly')}


# ---------------------------------------------------------------- locating inputs
def steam_libraries():
    home = os.path.expanduser('~')
    roots = [os.path.join(home, p) for p in (
        '.local/share/Steam', '.steam/steam', '.var/app/com.valvesoftware.Steam/.local/share/Steam')]
    roots += ['C:/Program Files (x86)/Steam', 'C:/Program Files/Steam']
    libs = []
    for root in roots:
        vdf = os.path.join(root, 'steamapps', 'libraryfolders.vdf')
        if os.path.isfile(vdf):
            libs.append(root)
            with open(vdf, encoding='utf-8', errors='replace') as f:
                libs += [p.replace('\\\\', '/') for p in re.findall(r'"path"\s+"([^"]+)"', f.read())]
    return list(dict.fromkeys(libs))


def find_game(arg):
    candidates = [arg] if arg else [os.path.join(l, 'steamapps/common/Project Wingman')
                                    for l in steam_libraries()]
    for c in candidates:
        if c and os.path.isfile(os.path.join(c, BASE_PAK)):
            return c
    sys.exit('Could not find Project Wingman. Pass --game "<...>/steamapps/common/Project Wingman"')


def collect_paks(mods_dir, tmp):
    """All .pak files under mods_dir, unpacking .zip (built in) and .rar/.7z (needs bsdtar,
    7z or unrar on PATH) into tmp first."""
    paks = []
    for dp, _, fs in os.walk(mods_dir):
        for f in fs:
            p = os.path.join(dp, f)
            low = f.lower()
            if low.endswith('.pak'):
                paks.append(p)
            elif low.endswith('.zip'):
                dest = os.path.join(tmp, f)
                with zipfile.ZipFile(p) as z:
                    z.extractall(dest)
                paks += _walk_paks(dest)
            elif low.endswith(('.rar', '.7z')):
                dest = os.path.join(tmp, f)
                os.makedirs(dest, exist_ok=True)
                if not _unpack(p, dest):
                    sys.exit(f'Cannot open {f}: install bsdtar (libarchive), 7-Zip or unrar, '
                             f'or extract it yourself into {mods_dir}')
                paks += _walk_paks(dest)
    return paks


def _walk_paks(d):
    return [os.path.join(dp, f) for dp, _, fs in os.walk(d) for f in fs if f.lower().endswith('.pak')]


def _unpack(archive, dest):
    for cmd in (['bsdtar', '-xf', archive, '-C', dest], ['7z', 'x', '-y', f'-o{dest}', archive],
                ['unrar', 'x', '-o+', archive, dest + os.sep]):
        if shutil.which(cmd[0]) and subprocess.run(cmd, capture_output=True).returncode == 0:
            return True
    return False


def identify(paks):
    found = {}
    for p in paks:
        name = os.path.basename(p).lower()
        for key, (_, match) in MODS.items():
            if match(name):
                found.setdefault(key, p)
    missing = [MODS[k][0] for k in MODS if k not in found]
    if missing:
        sys.exit('Missing original mod(s): ' + ', '.join(missing) + '\nSee README for download links.')
    return found


def table(pak, path):
    return DataTable(pak.read(path + '.uasset'), pak.read(path + '.uexp'))


# ---------------------------------------------------------------- the merge
def changes(base, mod, keep=lambda k: True):
    a, b = base.values(), mod.values()
    return {k: v for k, v in b.items() if k in a and a[k] != v and keep(k)}


def combine(*named):
    out = {}
    for who, d in named:
        for k, v in d.items():
            if k in out and out[k][1] != v:
                sys.exit(f'Conflict on {k}: {out[k][0]}={out[k][1]} vs {who}={v}')
            out[k] = (who, v)
    return {k: v for k, (_, v) in out.items()}


def apply(t, patches):
    for (row, path), v in sorted(patches.items()):
        if (row, path) in t.index and t.get(row, path) != v:
            t.set(row, path, v)


def merge(game_pak, mods):
    van = {p: table(game_pak, p) for p in (MAIN, MF, WEAP)}
    wso, aoa = Pak(mods['wso']), Pak(mods['aoa'])
    ub, uw = Pak(mods['unrel_base']), Pak(mods['unrel_weap'])

    wso_main = changes(van[MAIN], table(wso, MAIN))
    wso_mf = changes(van[MF], table(wso, MF))
    aoa_main = changes(van[MAIN], table(aoa, MAIN))
    aoa_weap = changes(van[WEAP], table(aoa, WEAP))
    unrel_main = changes(van[MAIN], table(ub, MAIN), lambda k: k[1] in UNREL_AIRCRAFT_FIELDS)
    unrel_weap = changes(van[WEAP], table(uw, WEAP), lambda k: k in UNREL_WEAPON_FIELDS)

    out = {}
    out[MAIN] = van[MAIN]
    apply(out[MAIN], combine(('More WSOs', wso_main), ('AoA', aoa_main), ('Unreleased', unrel_main)))

    # AoA ships a full 39-aircraft replacement for the Frontline 59 table. Layer the WSO seat
    # counts on top (its F59-specific values win for the 4 aircraft it set there) and the
    # unreleased-aircraft unlocks.
    out[MF] = table(aoa, MF)
    mf_wso = dict(wso_main); mf_wso.update(wso_mf)
    apply(out[MF], combine(('More WSOs', mf_wso), ('Unreleased', unrel_main)))

    out[WEAP] = van[WEAP]
    apply(out[WEAP], combine(('AoA', aoa_weap), ('Unreleased', unrel_weap)))
    return out


# ---------------------------------------------------------------- VVDoes additions
def vvdoes(out):
    """EUFB with 4 rounds; MiG-29 can carry it; PW-001 gets a weapon selector with a
    4th hardpoint for it (the airframe has 3 mount bones; the 4th spawns from the airframe)."""
    w = out[WEAP]
    w.set('EUFB', '.WeaponAmmo', 4)
    eufb_name = w.name_entry('EUFB')
    for path in (MAIN, MF):
        t = out[path]
        t.add_name(eufb_name)
        hp = t.get('MiG-29', '.HardpointCompatibilityList[3]')
        if 'eufb' not in hp.split(','):
            t.set_str('MiG-29', '.HardpointCompatibilityList[3]',
                      hp + ('' if hp.endswith(',') else ',') + 'eufb')
        t.set_name('PW-001', '.WeaponList[3]', 'EUFB')
        t.set('PW-001', '.FixedLoadout', 0)
        t.set_array('PW-001', 'HardpointSlots', [2, 1, 1, 1], 'int')
        t.set_array('PW-001', 'HardpointCompatibilityList', ['stdm', 'bmlaa', 'rgpd', 'eufb'], 'str')


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--mods', required=True, help='folder containing the original mod downloads')
    ap.add_argument('--game', help='Project Wingman install folder (auto-detected from Steam)')
    ap.add_argument('--out', default=OUT_NAME, help=f'output pak (default {OUT_NAME})')
    ap.add_argument('--install', action='store_true', help='also copy the pak into the game ~mods folder')
    ap.add_argument('--plain', action='store_true', help='merge only; skip the EUFB / PW-001 changes')
    a = ap.parse_args()

    game = find_game(a.game)
    print(f'game:  {game}')
    game_pak = Pak(os.path.join(game, BASE_PAK))
    with tempfile.TemporaryDirectory() as tmp:
        mods = identify(collect_paks(a.mods, tmp))
        for k, p in mods.items():
            print(f'mod:   {MODS[k][0]:45} {os.path.basename(p)}')
        out = merge(game_pak, mods)
    if not a.plain:
        vvdoes(out)
    files = []
    for path, t in out.items():
        t.check()
        files += [(path + '.uasset', t.ua), (path + '.uexp', t.ue)]
    write_pak(a.out, files)
    print(f'wrote: {a.out} ({os.path.getsize(a.out):,} bytes)')

    if a.install:
        mods_dir = os.path.join(game, 'ProjectWingman/Content/Paks/~mods')
        os.makedirs(mods_dir, exist_ok=True)
        shutil.copy(a.out, os.path.join(mods_dir, OUT_NAME))
        print(f'installed to {mods_dir}')
        clash = [p for p in _walk_paks(os.path.join(game, 'ProjectWingman/Content/Paks'))
                 if os.path.basename(p) != OUT_NAME and 'pakchunk' not in os.path.basename(p).lower()
                 and any(Pak(p).find(t + '.uexp') for t in (MAIN, MF, WEAP))]
        if clash:
            print('\nWARNING: these paks overwrite the same tables and will break the merge.')
            print('Move them OUT of the Paks folder (subfolders are still loaded):')
            for p in clash:
                print('  ' + p)


if __name__ == '__main__':
    main()
