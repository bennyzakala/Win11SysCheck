"""The English chapters of the user guide (``unislicer.manual`` builds the guide; same markup:
{t:key} = the interface's name, {k:id} = a shortcut's keys, {img:file|caption} = a picture).
Keep them in step with the Hebrew chapters: the tests check both name every menu command."""

CHAPTERS = (
    ("intro", "Welcome to UniSlicer", """
<p>UniSlicer prepares 3D models for printing: you open the files, arrange them on the plate, add supports
and a raft, slice them into layers and send them to the printer. It is made especially for dental labs -
temporary crowns and bridges, models, gingiva masks, splints and surgical guides - and also prints with
filament (FDM).</p>
{img:main.png|The main window: the toolbar on top, the models and their placement on the left, the 3D view in the middle and the settings on the right}
<h3>The usual way of working</h3>
<ol>
<li>Choose the printer, material and quality in the settings panel on the right.</li>
<li>Open the files ({t:open}, {k:open}) or drag them into the window.</li>
<li>Click {t:ocp_button} - every model is recognised, the plate is arranged and supported, and a summary
follows.</li>
<li>Check, and correct by hand where needed (supports, position, orientation).</li>
<li>Click {t:slice} ({k:slice}), then {t:export_print} ({k:export}) or send it to a network printer.</li>
</ol>
"""),
    ("install", "Installing and updating", """
<p>The installer is at:<br><code>https://github.com/bennyzakala/Win11SysCheck/releases/download/unislicer-latest/UniSlicer-Setup.exe</code></p>
<p>When a new version is out, an {t:upd_available} button appears in the status bar at the bottom of the window. Clicking it
downloads the version, checks that the file is whole and genuine (SHA-256) and installs it. You can also check
by hand: {t:more} → {t:upd_check_now}. {t:upd_check_start} checks at every start.</p>
<p>The version number (for example 1.0.215) is shown in the update window and in the job report. Every change
to the program gets a new number.</p>
"""),
    ("screen", "The main window", """
<h3>The top bar</h3>
<ul>
<li>{t:open} - open files. {t:shapes} - ready-made shapes, {t:add_text} and {t:void_title}.</li>
<li>{t:ocp_button} and {t:auto_prepare} - the plate in one click (chapter 10).</li>
<li>{t:arrange} - arranging on the plate; its arrow opens {t:auto_layout}, {t:lay_fill} and {t:align_menu}.</li>
<li>{t:duplicate}, {t:delete}, {t:undo} and {t:redo}.</li>
<li>{t:more} - every other command: projects, export, network, mesh repair, the user guide and more.</li>
<li>In the middle: switching between {t:prepare} and {t:preview} (the layers after slicing).</li>
<li>On the right: {t:assistant}, {t:theme} (light / dark / your lab's logo) and the language (עברית, English,
Русский, العربية).</li>
</ul>
<h3>The menu bar</h3>
<ul>
<li>{t:menu_file}: {t:new_project} ({k:new_project}), {t:open}, {t:open_project}, {t:recent_files}, {t:save_project}, {t:slice}, {t:export_print},
{t:export_as}, {t:export_stl}, {t:report_save}, the print library, {t:net_title}, {t:queue_title} and {t:menu_exit}.</li>
<li>{t:menu_edit}: {t:undo}, {t:redo}, {t:select_all}, {t:duplicate}, {t:multiply}, {t:delete}, arranging and
aligning, {t:center}, {t:move_to}, {t:scale_to}, {t:rename}, {t:model_settings}, {t:merge_models}, {t:hide_models}
and {t:show_all_models}.</li>
<li>{t:menu_info}: {t:assistant}, {t:estimate_title}, {t:manual_menu} ({k:manual}), {t:menu_shortcuts},
{t:menu_updates} and {t:menu_about}.</li>
</ul>
<h3>The left panel</h3>
<p>The {t:objects} list (pick several with Ctrl or Shift) and below it {t:transform}: position, rotation, scale
and size in mm, the {t:rotate_90} buttons, {t:auto_orient}, {t:lay_flat}, {t:center} and {t:reset_transform}.
For resin there is also the {t:supports_card} card. {k:panel_left} hides and shows the panel.</p>
<h3>The 3D view</h3>
<p>Along the side of the view is the toolbar: Move ({k:tool_move}), Rotate ({k:tool_rotate}), Face to plate
({k:tool_face}), Drain hole ({k:tool_hole}), Support keep-out ({k:tool_keepout}), Label ({k:tool_label}) and
Measure ({k:tool_measure}). On top: {t:view_iso}, {t:view_top}, {t:view_front}, {t:view_side} and {t:view_fit},
plus {t:overhangs}, showing surfaces without supports, and the section view. The cube in the corner turns the
view when you click one of its faces.</p>
<h3>The settings panel</h3>
<p>At the top you pick the printer family: FILAMENT (FDM), RESIN (LCD / DLP / SLA) or UV (PolyJet). Below:
the {t:printer} and its plate, the {t:material}, the {t:quality} and Stable / Fast mode, then a settings search
box, a switch between the simple and the full view, and every setting by topic. {t:model_settings} (in the
{t:menu_edit} menu) gives one model settings of its own. A setting you changed gets a reset button, and
hovering over a name explains it. {k:panel_right} hides and shows the panel. At the bottom: {t:slice},
{t:export_print} and sending to a network printer.</p>
"""),
    ("files", "Opening files and the model type", """
<p>Formats: STL, OBJ, 3MF, PLY, AMF, and a ZIP file with models inside. Open them with {t:open} ({k:open}), by
dragging them into the window, or from {t:more} → {t:recent_files}.</p>
<p>When you open files, the {t:ap_title} window lets you choose each file's type (crown / bridge,
model, splint and more). The type is suggested from the file name: a lab code with a tooth range (such as
FYQ-SOD…-16-16-1) is a crown or bridge, <code>_Antag</code> is an antagonist, <code>_Tooth_14</code> is a die,
<code>_Tissue</code> is a gingiva mask. When the name says nothing, the shape decides. You can turn the window
off with {t:more} → {t:ap_show_menu}.</p>
<h3>What happens by itself when a file loads</h3>
<ul>
<li><b>{t:open_up_menu}</b> - crowns and bridges are turned so the opening faces up (the inside never touches
a support).</li>
<li><b>{t:tissue_up_menu}</b> - gingiva masks are laid with the flat side up.</li>
<li><b>{t:auto_close_holes}</b> - small holes in the mesh are closed. A crown whose margin was left unjoined is
mended so the crown's opening stays open.</li>
<li>Every other model keeps the orientation it came with in the file.</li>
</ul>
<p>These three are in the {t:more} menu and each can be turned off. Red lines on a model mark an open or broken
mesh ({t:show_defects}); {t:repair_mesh} and {t:repair_advanced} mend it.</p>
"""),
    ("printer", "Printer, material and quality", """
<p>Choose the {t:printer} from the list (grouped by maker; a star marks favourites). The plate in the view, the
file format and the exposure settings follow the printer. The full list of printers is in appendix D.</p>
<p>Under {t:material}, the resins tested on this printer come first, with ★. For resins with a maker's table
(Phrozen DS, for instance) choose {s:exposure_mode}: <b>Stable</b> or <b>Fast</b>, and the
exposure and motion come from the table for the printer and the layer height.</p>
<p>Under {t:quality} you choose the layer height. Every setting you change is kept; you can save the combination
as your own profile ({t:save_printer} / {t:save_material}).</p>
<p><b>Formlabs:</b> Formlabs printers print only jobs PreForm prepared. Choose the printer and the resin (such as
Grey V5 or White V5), arrange in UniSlicer, and send with {t:more} → {t:fl_menu} (chapter 12).</p>
"""),
    ("place", "Position, orientation and layout", """
<p><b>Moving:</b> drag a model on the plate, or type a position in the left panel. Height above the plate (Z)
lifts the model.</p>
<p><b>Turning:</b> the Rotate tool ({k:tool_rotate}) shows three rings; drag a ring to turn about its axis
(Shift - in 15° steps), with the angle shown next to the cursor. You can also type angles, click
{t:rotate_90}, or use {t:lay_flat} and the Face tool ({k:tool_face}) - click a face and it is laid on the plate.</p>
<p>{t:auto_orient} looks for the orientation that needs the fewest supports; for resin it prefers a tilt, so no
large surface lies parallel to the screen. Right-clicking a model opens a menu with ready orientations, mirror,
direct print on the plate and more.</p>
<h3>Arranging the plate</h3>
<ul>
<li>{t:layout_quick} ({k:arrange}) - arranges every model with the options learned so far.</li>
<li>{t:auto_layout} ({k:auto_layout}) - a window with the method (rows, true outlines or a grid), spacing,
margin, turning and the starting corner.</li>
<li>{t:lay_fill}, {t:multiply}, {t:duplicate} ({k:duplicate}) and {t:align_menu}.</li>
</ul>
<p>The layout leaves room for the raft, and for the tab of a raft &amp; label raft.</p>
"""),
    ("supports", "Supports", """
<p>For resin, the {t:supports_card} card in the left panel has everything:</p>
<ul>
<li>{t:sup_auto} - support points for the selected model, or for all when none is selected. Supports are built
in the background and the view stays smooth.</li>
<li>{t:sup_add} ({k:sup_add}) - every click on the model adds one support at the green dot. Esc to finish.</li>
<li>{t:sup_remove} ({k:sup_remove}) - clicking a support removes it.</li>
<li>{t:sup_manual} - starts with no supports and adds one per click. {t:sup_clear} deletes them all.</li>
<li>{t:raft_menu} - raft type, thickness, margin, slope and height (chapter 8).</li>
</ul>
<p>{s:sla_model_type} (in the support settings) sets the supports: crowns / bridges, splints, guides and dentures
get no supports on the fitting surface. {s:sla_support_preset} - light, medium or heavy. {s:sla_support_minima} puts a support at every edge that starts in mid-air, so no part is left unsupported.</p>
<p>The {k:tool_keepout} tool marks an area that gets no supports. The button for unsupported surfaces paints red
every overhang no support reaches. A model printed directly on the plate (right click → direct print) gets
supports only once direct printing is turned off.</p>
{img:supports.png|A crown with supports and a raft &amp; label raft}
"""),
    ("raft", "The raft and the case code", """
<p>Raft types ({s:sla_raft_type}): solid, cross grid, hexagonal, mini (a small pad for each group of
supports), mini tiny (inside the tooth outline), <b>raft &amp; label</b>, <b>solid lite</b> and none.</p>
<h3>Solid lite</h3>
<p>A solid raft along the model's outline, 0.8 mm around it, with every support foot on it. Round every small
round part standing out of the outline - a ball at a bridge's end, for instance - the raft reaches about 2 mm
further out, so the round part stands inside the raft instead of over its edge. Plain corners and the rounded
outline of a whole tooth get no bulge.</p>
<h3>Raft &amp; label</h3>
<p>A narrow raft along the model's outline, 0.8 mm around it, with every support foot on it. At the model's end
(toward the right of the plate, or the back for a part lying along the plate) there is a crescent tab 5 mm wide
carrying the case code, embossed 0.4 mm. The code is taken from the SOD number in the file name (46614-044-P from
DAA-SOD260046614-044-P-…), and for a file without one, from the file name. If you switch to this raft after the
parts were arranged close together, click {t:layout_quick} ({k:arrange}) again to make room for the tab.</p>
{img:raft_label.png|Raft &amp; label: a narrow raft and a crescent tab with the code}
<h3>The code on other rafts</h3>
<p>{s:sla_raft_label_teeth} puts the code on crown and bridge rafts, and {s:sla_raft_label} puts the file name. {s:sla_raft_label_side}: underside (engraved and mirrored) or top
(embossed).</p>
<p>{s:bottom_xy_auto} (on by default): for a part printed straight on the plate, such as a model, the bottom
layers are pulled in by 0.2 mm so a label engraved in the base stays open and the base does not spread. Rafts are
not touched, and a value set by hand in {s:bottom_xy_compensation} takes over.</p>
<p class="note">The bottom layers are exposed for a long time, and the strong light tends to fill an engraving on
the underside. If the code does not come out in the print, an embossed code on top, or raft &amp; label, works
better.</p>
"""),
    ("tools", "Model tools", """
<ul>
<li><b>Hollowing and drain holes</b> (settings → Hollowing &amp; drain holes): closed cavity, open at the bottom or
solid base; add a drain hole with the {k:tool_hole} tool by clicking the model.</li>
<li><b>Label on the model</b> ({k:tool_label}): click the spot and type; Ctrl + wheel changes the size, Alt + wheel
turns it.</li>
<li><b>Measure</b> ({k:tool_measure}): two clicks - the distance is shown in the view.</li>
<li>{t:cut_height}, {t:split_parts}, {t:repair_mesh}, {t:repair_advanced}, {t:texture_title}.</li>
<li>{t:cutter_role} - a part that is not printed but cut out of the models it overlaps (for engraving and holes
of any shape).</li>
<li>{t:void_title} and {t:add_text} in the {t:shapes} menu.</li>
</ul>
"""),
    ("oneclick", "One-click plate and auto prepare", """
<p>{t:ocp_button}: recognises each model's type, arranges the plate and creates supports, then shows a summary
with a fix for every warning (a model off the plate, a part without support, a cup that holds resin and more)
before slicing. The models' orientation is not changed.</p>
<p>{t:auto_prepare}: also orients every model automatically, then arranges and supports them.</p>
"""),
    ("slice", "Slicing and the preview", """
<p>{t:slice} ({k:slice}) slices the plate into layers. While you work the program slices in the background, so the
slice is usually ready at once. After slicing, switch to {t:preview}: drag the layer slider or use {k:layer_up}
and {k:layer_down}, and {t:layers_play} plays the layers. Below them are the print time, the amount of material,
the weight and the cost; the details are in {t:more} → {t:estimate_title}.</p>
<p>The section view cuts the models at the height you choose (or across, X / Y) to look inside.
{t:pixel_edit} lets you fix pixels in a layer before export.</p>
{img:preview.png|The layer preview}
"""),
    ("export", "Export and sending to the printer", """
<p>{t:export_print} ({k:export}) saves the print file in the printer's format: PRZ for Phrozen, CTB, GOO for
Elegoo, SL1, CHITUBOX ZIP, SLC / CLI for industrial SLA, 3MF for PreForm / GrabCAD, and G-code for FDM. The file
goes to the <b>print library</b> ({t:export_library_open} / {t:export_library_choose}), and {t:export_as} saves it
somewhere else. If the plate changed after slicing, slice again before exporting (otherwise the last slice is
exported, with a message).</p>
<ul>
<li>{t:report_save} - a job report with a picture, times and material; {t:report_auto} saves one with every
export.</li>
<li>{t:project_auto} - saves a project file next to every print file, so the plate can be opened again.</li>
<li>{t:export_stl} - the models with their supports as STL.</li>
<li>{t:net_title} - finds printers on the network and sends to them directly; {t:queue_title} - several jobs in a
row.</li>
<li>{t:fl_menu} - through PreForm Server: open PreForm → File → Start PreForm Server, click Connect, choose the
printer and the resin (picked for you from the resin chosen in UniSlicer) and send.</li>
<li>{t:remote_title} - watch and control the network printers from a phone or a browser with a Google account
(UniSlicer must stay open on the computer).</li>
<li>{t:cad_title} - set a folder as the design software's export folder; every new file that arrives there is
loaded, recognised by job type and prepared automatically (orientation, supports and layout).</li>
</ul>
"""),
    ("projects", "Projects, undo and preferences", """
<p>{t:new_project} ({k:new_project}) clears the whole plate for a new job; the printer, the material and the
settings stay, and {k:undo} brings every model back. {t:save_project} ({k:save_project}) saves the whole plate - models, positions, supports and settings;
{t:open_project} opens it. {t:undo} ({k:undo}) and {t:redo} ({k:redo}) work for every action, supports and
loading included.</p>
<p>{t:mouse_menu}: {t:mouse_chitubox} or {t:mouse_classic} (appendix B). {t:theme}: light, dark, a branded theme
and {t:theme_logo} to show your lab's logo.</p>
"""),
    ("assistant", "The assistant", """
<p>The {t:assistant} checks the models in the background, learns from your changes (layouts, support corrections,
settings) and suggests improvements in the assistant panel. A plate layout you taught it is used by
{t:auto_layout}.</p>
"""),
    ("fdm", "Filament printing (FDM)", """
<p>With an FDM printer the settings for walls, infill, temperature, speed and retraction appear.
{t:fil_title} sets several filaments and colour changes (Creality CFS included), and each model gets a filament
of its own. The output is G-code with a preview picture.</p>
"""),
    ("trouble", "Troubleshooting", """
<table class="grid">
<tr><th>Problem</th><th>What to do</th></tr>
<tr><td>A part is left in mid-air without support</td><td>Show the unsupported surfaces and add supports with
{k:sup_add}; check that {s:sla_support_minima} is on.</td></tr>
<tr><td>The code on the raft does not come out in the print</td><td>Raft &amp; label, or {s:sla_raft_label_side}: top, embossed.</td></tr>
<tr><td>A crown's openings were closed</td><td>{t:auto_close_holes} keeps the crown's opening; if it still
happens, turn it off and send the file in for a check.</td></tr>
<tr><td>A crown loaded upside down</td><td>Is {t:open_up_menu} on? Turn it by hand, or turn the option off.</td></tr>
<tr><td>Models touch each other</td><td>{t:layout_quick} ({k:arrange}) or more spacing in {t:auto_layout}.</td></tr>
<tr><td>Slow on an old computer</td><td>Without OpenGL 3.3 the program switches to software drawing by itself;
updating the graphics driver makes it faster.</td></tr>
<tr><td>An error message appeared</td><td>The assistant catches the error, keeps the program open and explains
what happened and how to fix it.</td></tr>
</table>
"""),
)
