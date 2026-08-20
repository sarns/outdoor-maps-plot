# Third-party notices

This project depends on third-party software distributed under its own license
terms. The authoritative license texts included with each installed package or
container image take precedence over this summary.

## pypdfium2 and PDFium

`pypdfium2` is available under the Apache License 2.0 or the BSD 3-Clause
License. Its pre-built wheels include PDFium, which uses a BSD-style license,
and license notices for PDFium's bundled dependencies.

- <https://pypdfium2.readthedocs.io/en/stable/readme.html#licensing>
- <https://github.com/pypdfium2-team/pypdfium2>

Binary redistributions must retain the license material shipped in the
`pypdfium2` wheel.

## fitdecode

`fitdecode` is used to decode user-provided FIT activity and course files. It
is distributed under the MIT License.

- <https://github.com/polyvertex/fitdecode>

## lib3mf

`lib3mf` is the 3MF Consortium's reference implementation used to create and
validate standards-compliant 3MF model packages. It is distributed under the
BSD 2-Clause License and includes libzip, zlib, base64, and fast_float.

- <https://github.com/3MFConsortium/lib3mf>
- <https://github.com/3MFConsortium/lib3mf_python>

## Map data and rendered tiles

Generated posters include attribution for the selected provider and its data
sources. Using a tile service is also subject to that provider's current terms
and usage policy:

- OpenTopoMap: <https://www.opentopomap.org/about>
- Esri: <https://www.esri.com/en-us/legal/terms/full-master-agreement>
- Stadia Maps: <https://stadiamaps.com/terms-of-service/>
- Thunderforest: <https://www.thunderforest.com/terms/>

## Elevation data

3D relief models use the public Mapzen Terrain Tiles dataset hosted by the AWS
Open Data program. The tiles combine several regional and global elevation
sources, each with its own required attribution. Users must retain the
applicable source credit for the region represented by a generated model.

- Dataset: <https://registry.opendata.aws/terrain-tiles/>
- Source attribution: <https://github.com/tilezen/joerd/blob/master/docs/attribution.md>
