# Program card design kit — start here

This kit contains starter drawings, artwork preparation instructions, and instructions for using your generated PCB manufacturing files.

## Prepare your artwork

The `.ai` file is a starter drawing for Illustrator. The `.svg` file is a starter drawing for Inkscape. You can also create your own SVG in another vector drawing app; using these files is optional.

Use a 19 × 11 mm page. All visible vector artwork is included, regardless of layer names. The Inkscape starter drawing has a CONTENT layer for convenience, plus PCB, OUTLINE and SAFETY reference layers. Hide or delete the reference layers before saving your finished design.

Convert live text and strokes to filled paths or outlines. See `Inkscape-program-card-instructions.md` for the Inkscape steps. Artwork beyond the page edge is clipped.

## Generate and check your files

Upload one SVG to use the same design on all four card positions, or four SVGs for four different designs, at https://pcg.musicthing.co.uk/.

Check the generated proof before ordering. Download the output ZIP and extract it. It contains `Gerbers.zip` (the PCB manufacturing drawings), `bom.csv`, `positions.csv`, and `readme.md` (ordering instructions). Check the Gerbers in a Gerber viewer as well, especially for detailed artwork.

## Order your cards

Read `PCB-ordering-instructions.md` in this kit before ordering. The same instructions are supplied as `readme.md` with each generated output.

Upload `Gerbers.zip` to your manufacturer's PCB quote page. The supplied ordering instructions describe the required board thickness, finish, assembly side, and how to use the BOM and component positions files. Check the manufacturer's preview and selected options before placing an order.
