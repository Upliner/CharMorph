# CharMorph

CharMorph is a character creation tool for Blender.
It uses base meshes and morphs from ManuelbastioniLAB/MB-Lab while being designed for easy creation of new models and modification of existing ones.

This addon reimplements most of MB-Lab's features, but it currently does not contain any MB-Lab code.
It uses a different database format and other internal differences, as well as less hard coded features.   

It is planned that CharMorph won't be limited to humanoids. Animals and other creatures are welcome at CharMorph too.

## Options:

* **Use local materials:**

  Make a copy of local materials instead of importing them every time.

  It is safe to use this if you're creating a scene from scratch, but it is recommended to disable this option if you already have MB-Lab/older Charmorph characters in your scene.

## Differences from MB-Lab:

* Noticeably improved performance
* Direct setting of skin and eyes color
* Material displacement instead of displacement modifier.
  This means there will be no real displacement in EEVEE, but a nice live preview with bumps is available.  
  In Cycles, the skin material is set to "Displacement and bump" by default.
* Hairstyles
* Realtime asset fitting with combined masks
* Rigify support with full face rig
* Alternative topology feature for applying morphs to models with different topology
* Characters are created at the 3D cursor's location, not always at world origin

## Downsides

* Rig is added only at finalization, because it takes quite a long time for Rigify to generate a rig and I have no idea if it's possible to morph such rig in real time.
* Still lacking some features (Automodelling, measures) just because I don't use them in my projects. Maybe they'll be implemented later.

## Development notes

This project uses git submodules, so you need to use `git clone --recursive` when cloning this repository. If you forgot to do so, you can execute the following commands individually after cloning:
```
cd CharMorph
git submodule init
git submodule update
cd data
git submodule init
git submodule update
``` 

## Installation manual

* Download the latest `charmorph.zip` package from the [releases page](https://github.com/Upliner/CharMorph/releases/latest) (not the source code file but the release package).   
* In Blender go to Edit->Preferences->Addons, click "Install..." and select the downloaded zip package.

**NOTE:** If the zip file is smaller than 10MB, it means the file contains the addon only, without the character library. If that's the case, you can download it from [here](http://github.com/Upliner/CharMorph-db/) and extract it to the CharMorph data directory, which should be located in `%appdata%\Blender Foundation\Blender\<VersionNumber>\scripts\addons\CharMorph\data` on Windows

## Links

* Features showcase on this [BlenderArtists forum thread](https://blenderartists.org/t/charmorph-character-creation-tool-mb-lab-based/1252543)
* Discord server: https://discord.gg/bMsvxN3jPY
