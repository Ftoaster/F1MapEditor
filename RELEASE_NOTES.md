Mapeditor is a Windows editor for designing your own tracks for F1 25.

## New in 0.2.0

- **Build for Swofty Launcher:** an addon with the track's own name in the game's menus, no installer slots, as many tracks as you like
- **Install and start from the editor**, with a crash summary after each session
- **Laps, fuel, tyres and AI lap times** are worked out from the track
- **Grid option for 26 cars:** grid slots, garages and pit boxes on your track
- **New check KI-07:** finds AI routes that crash the game as soon as an AI car drives them
- The build dialog only shows the buttons you can use right now

**Unofficial fan project, not affiliated with EA, Codemasters, Formula One Group or the FIA. Contains no game files.**

## Download

`Mapeditor-0.2.0-win64.zip`: unpack the whole folder and start `Mapeditor.exe`. The program is not signed, so Windows may
show a warning.

## What you need

- Windows 10 or 11, 64-bit
- [F1 25 on Steam](https://store.steampowered.com/app/3059520/F1_25/), to build packages
- [Algarve International Circuit (Portimao) for F1 25](https://www.overtake.gg/downloads/algarve-international-circuit-portimao-for-f1-25.87333/) (`portimao.zip`), **version 1.0.0 or 1.0.2**, **not included**, as the base for building
- An installer, one of these:
  - **[Swofty Launcher](https://f1.swofty.net/) (recommended):** own track name in the menus, install and start from the editor. Modded play runs offline and without EA AntiCheat
  - [EGO GameDatabases Editor](https://apps.sztefek.eu/app/ego-gamedatabases-editor) 1.6.0 or newer, to install a track and activate it once

Drawing and checking tracks works without the game.

## Highlights

- Draw tracks from control points or segments, edit height, banking and cross-section
- Racing line, braking points, corners, sectors, DRS and Straight Mode zones
- Environment objects, hills and mountains
- Own ground and surroundings textures generated from your track
- Rule check before building, quick swap with backup for testing

## Known limitations

- **Pit lane on the right only.** On the left, cars and pit crews stand on the wall side until the car leaves.
- Lighting comes from the carrier track.
- Version 1.0.0 of the Portimão mod can crash on AMD graphics cards, which its version 1.0.2 fixes. Tracks built on 1.0.2 are tested in Time Trial and in Grand Prix with AI pit stops. Whether they run on AMD cards is not tested.
- Swofty Launcher: if you remove a track addon, start a new Grand Prix instead of Continue. A saved session on a track that is gone crashes the game.
- The 26-car option only adds the places to the track. The cars themselves need a grid addon, and tracks of the original game hold 24.
- Laps, fuel, tyres and AI times from the track are tested through Swofty Launcher, not yet through the EGO installer. The tyre choice is an estimate.

Use offline and at your own risk. Back up your save data and game database first.

## Licence

GPL-3.0-only for this project's code, see `LICENSE`. The source code of this release is in this repository at tag `v0.2.0`.
