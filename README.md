# CharMorph

CharMorph is a character creation tool for Blender.

It uses base meshes and morphs from ManuelbastioniLAB/MB-Lab but it's designed for easy creation of new models and easy modification of existing ones.

This addon includes from-scratch reimplementaion of most of MB-Lab features but it currently doesn't contain any MB-Lab code.
It uses different database format and has more internal differences.
It uses much less hard coded features. It is planned that CharMorph won't be limited to humanoids. Animals and other creatures are welcome at CharMorph too.

## Options:

* **Use local materials:**

  Make a copy of local materials instead of importing them every time.

  It is safe if you're creating scene from scratch, but it is recommended to disable this option if you already have MB-Lab characters on the scene.

## Differences from MB-Lab:

* Direct setting of skin and eyes color
* Material displacement instead of displacement modifier.
  No real displacement in EEVEE, but nice live preview with bumps is available.
  In Cycles skin material is set to "Displacement and bump" by default.
* Hairstyles
* Realtime asset fitting with combined masks
* Rigify support with full face rig
* Characters are created at 3D cursor location, not always at world origin
* Performance is noticably better

## Downsides

* Rig is added only at finalization, because it takes quite a long time for Rigify to generate a rig and I have no idea if it's possible to morph such rig in real time.
* Still lacking some features (Automodelling, measures) just because I don't use them in my projects. Maybe they'll come later.

## Development notes

This project uses git submodules so you need to checkout submodules alongside with code:

git clone https://github.com/Upliner/CharMorph
cd CharMorph
git submodule init
git submodule update
cd data
git submodule init
git submodule update

## Links

You can see examples of these features at [BlenderArtists forum thread](https://blenderartists.org/t/charmorph-character-creation-tool-mb-lab-based/1252543)
