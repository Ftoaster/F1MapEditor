Mapeditor is a Windows editor for designing your own tracks for F1 25.

## New in 0.3.7

- **Straight lines and corners:** Edit → Make corner gives control points a fixed radius (15 m by default); the track runs dead straight between corners and closes the lap without an unwanted bend. Edit → Align to line lines up a row of points exactly
- **Live map under the track** in true metres, satellite or street: Project → Live map. Contributed by [Ftoaster](https://github.com/Ftoaster) in PR #3, thank you!
- **F1 25 found in every Steam library**; otherwise the editor asks for the game folder once and remembers it (Tools → Set F1 25 game folder …)
- **Better checks for ovals and short straights:** warning when the starting grid reaches into a corner (SA-01), pit lane limited to the straight automatically (BO-06), no more false ZO-04 warning at the finish line

## New in 0.3.6

- **Carrier package setup:** if the Portimão mod is missing, the editor asks and guides you: it opens the Overtake download page (a free account is needed; the editor never asks for your login), watches your Downloads folder read-only, checks the finished ZIP and keeps one copy for all projects. Also under Tools → Set up carrier package …

## New in 0.3.5

- **Easier Swofty install:** "Install in launcher" installs the shared carrier (shown as "type: files" in Swofty) together with your track if it is missing. The carrier holds only textures; your track is the other ZIP and needs it
- **swofty.exe remembered** for all projects
- **Clearer Build window:** status card with phase chain and times, the next step as the main button, messages sorted by severity, fields checked while typing, and a compact layout that scrolls instead of growing beyond the screen
- **Build in the background:** hide the Build window while it builds; progress shows in the main window

## New in 0.3.4

- **Smoother zoom:** the mouse wheel zooms around the point under the cursor, at the same pace on every wheel and touchpad

## New in 0.3.3

- **Clean asphalt:** tracks built with world maps no longer show brown-green dirt patches on the road, kerbs and pit lane. Rebuild your track to get the fix

## New in 0.3.2

- **Replays work on your own tracks:** the build moves the carrier's replay, TV and pit stop cameras and their trigger zones to the new track
- **Cameras tool (K):** see which cameras each trigger zone switches to, drag cameras freely and zones along the track, or let the editor calculate them for the current track

## New in 0.3.1

- **Load a track from the game:** New from template lists all F1 25 tracks (read-only). Besides centre line, heights and widths it can take over the pit lane, DRS and Straight Mode zones, the starting grid, kerbs, gravel and barrier distances, and the scenery as environment objects. Packages made from an original track are for your own use only
- **Better fit into the game world:** templates are rotated first and only scaled down if needed; the game world offset is set automatically
- **English first:** the editor now starts in English; German is still available under View → Language

## New in 0.3.0

- **Trace a track from an image:** load a map picture or paste a screenshot, measure the scale on its scale bar, click a few support points in driving direction, and the editor finds the track between them. It only takes over a lap that is complete, does not cross itself and drives no part twice
- **Scale track:** make the whole track larger or smaller by a factor, or bring it to a target length in metres (Edit menu, or the button under Properties → Road)
- **Combine tracks** (since 0.2.2): copy and paste track points between editor windows

**Unofficial fan project, not affiliated with EA, Codemasters, Formula One Group or the FIA. Contains no game files.**

## Download

`Mapeditor-0.3.7-win64.zip`: unpack the whole folder and start `Mapeditor.exe`. The program is not signed, so Windows may
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

- Pit lane on the left of the track is tested through Swofty Launcher, not yet through the EGO installer.
- Lighting comes from the carrier track.
- Version 1.0.0 of the Portimão mod can crash on AMD graphics cards, which its version 1.0.2 fixes. Tracks built on 1.0.2 are tested in Time Trial and in Grand Prix with AI pit stops. Whether they run on AMD cards is not tested.
- Swofty Launcher: if you remove a track addon, start a new Grand Prix instead of Continue. A saved session on a track that is gone crashes the game.
- The 26-car option only adds the places to the track. The cars themselves need a grid addon, and tracks of the original game hold 24.
- Laps, fuel, tyres and AI times from the track are tested through Swofty Launcher, not yet through the EGO installer. The tyre choice is an estimate.
- Swofty Launcher: when you drive into the pits yourself (pit assist off), the game shows no gate to your pit crew. Under the EGO installer it did. The cause is not found yet.

Use offline and at your own risk. Back up your save data and game database first.

## Licence

GPL-3.0-only for this project's code, see `LICENSE`. The source code of this release is in this repository at tag `v0.3.2`.
