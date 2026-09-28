# VVDoes Project Wingman merge

Three popular Project Wingman mods that normally cancel each other out, merged so all three work at once, plus a few extras. This repo contains only a build tool, with no game files and no other modders' files. You download the original mods yourself, run one command, and it builds the merged `.pak` from your own game install.

## What you get

**The three mods, working together:**

| Mod | By | What it does |
|---|---|---|
| [More WSOs](https://www.nexusmods.com/projectwingman/mods/566) | see mod page | A back-seater (WSO) on 17 more aircraft |
| [AoA for All and Mk2 planes for Campaign (F59)](https://www.nexusmods.com/projectwingman/mods/707) | see mod page | AoA on every aircraft, Mk2 variants buyable in campaign, EUFB/RDBM outside Conquest |
| [Playable Unreleased Planes](https://www.nexusmods.com/projectwingman/mods/17) | Callsign-YukiMizuki | Unlocks A-10A, F-18F, Su-30, F-15E, J-10B, FT-15, RF-1, Su-47, Su-57, E-3, X-16 and the Conquest variants at 0 credits, plus BML-U weapons |

All credit for these goes to their authors. Download them from the links above.

**VVDoes additions** (skip them with `--plain`):

- **PW-001 weapon selector.** The PW-001's fixed loadout is replaced with a selector: hardpoint 1 missiles, 2 BML-AA, 3 railgun, and a new **hardpoint 4 carrying the EUFB**.
- **MiG-29** can carry the EUFB on its special-weapon hardpoint.
- **EUFB ammo raised from 2 to 4.**

Everything applies to the main campaign and to Frontline 59.

### Why they didn't work together

All three mods replace the same game files: the aircraft table, the Frontline 59 aircraft table and the weapons table. The game loads only one copy of each file, so whichever mod loads last wins and the others are ignored. This tool takes just the values each mod actually changes and applies them all to one copy of the tables.

Playable Unreleased Planes was made for a 2021 game version, and its tables are missing 4 aircraft and several fields added since. Installing it directly would roll those back. The tool takes only its unlock and price changes and applies them to the current game's tables.

## Install

Requires **Python 3.8+** and no other packages. It works on Windows and Linux (including Steam Deck / Proton). On Windows, install Python from [python.org](https://www.python.org/downloads/) and tick **"Add python.exe to PATH"** in the installer.

1. Download the three original mods from the links above into one folder, e.g. `Downloads/pw-mods`. Leave them zipped: `.zip` files are opened automatically. The Unreleased Planes `.rar` needs 7-Zip or `bsdtar` installed, or you can extract it into the same folder yourself.
2. Get this tool: **Code → Download ZIP** on this page, then unzip it.
3. Run it from the tool's folder:

   ```sh
   python3 build.py --mods ~/Downloads/pw-mods --install
   ```

   On Windows, use `py build.py --mods "%USERPROFILE%\Downloads\pw-mods" --install`.

   It finds your Steam install automatically. If it can't, add `--game "<path to>/steamapps/common/Project Wingman"`.

4. **Remove the original mod paks from the game folder.** If they're still installed they overwrite the merge. `--install` lists any it finds. Move them *out of* `Content/Paks` completely: the game also loads paks from subfolders.

Without `--install`, it writes `~~Merged_WSO_AoA_Unreleased_P.pak` to the current folder and you copy it into `ProjectWingman/Content/Paks/~mods/` yourself.

### Options

| Flag | |
|---|---|
| `--mods DIR` | Folder with the original downloads (required) |
| `--game DIR` | Game install folder, if auto-detect fails |
| `--out FILE` | Where to write the pak |
| `--install` | Also copy it into the game's `~mods` folder |
| `--plain` | Merge only, without the VVDoes additions |

## Achievements: use at your own risk

Some achievements still unlock with this mod installed, but not all of them reliably. **If you're going for achievements, uninstall the mod first.** That's the safer option.

To uninstall, delete `~~Merged_WSO_AoA_Unreleased_P.pak` from `Content/Paks/~mods/`. Back up your saves before switching between modded and unmodded play. Modded saves can contain planes that don't exist in the normal game.

## After a game update

If a patch changes these tables, rerun `build.py`. It always builds from your current install, so it picks up the new tables. If it stops with a `Conflict` or a parse error, open an issue.

## Notes

- The PW-001 model has only three weapon mount points, so the EUFB on hardpoint 4 has no pylon of its own. It works in-game.
- Some Unreleased Planes aircraft are unfinished by the original author's own description: some are barely flyable or missing weapons.
- Tested against the Steam build current as of September 2026.

## Planned: achievement fix

Separately from this mod, the difficulty-completion achievements don't register for me on a completely normal playthrough. I finished a full Mercenary playthrough and replayed missions from the mission selector, and the game still doesn't recognise that I've beaten every mission on Easy, Normal or Hard. The hidden achievement, which I'm fairly sure is the Mercenary one, never unlocked either. In the future I want to try patching the game so these achievements register properly. If you've run into the same thing or know how the game tracks mission completion, get in touch.

## Contact

VirtualVince: [contact@virtualvince.ca](mailto:contact@virtualvince.ca) · [github.com/VirtualVince](https://github.com/VirtualVince)

Bug reports and ideas are welcome, by email or as a GitHub issue.

## How it works

`pwmod/pak.py` reads and writes Unreal Engine 4 `.pak` archives. `pwmod/datatable.py` parses and edits the cooked DataTable assets. `build.py` holds the recipe: which fields to take from which mod, and the VVDoes changes. The build refuses to continue if two mods set the same field to different values.
