// =====================================================================
//  Astronaut Cam - printable astronaut with a Wi-Fi pan/tilt camera
//  inside the helmet.
//
//  - Pan  : SG90 servo inside the torso turns the whole helmet (0-180 deg)
//  - Tilt : SG90 servo inside the helmet nods the camera behind the visor
//  - Brain: ESP32-CAM (AI-Thinker) + OV2640 with a 75 mm flex cable
//
//  Units: mm. Front of the astronaut is -Y, up is +Z.
//  Export a single part:  openscad -D 'part="torso"' -o torso.stl astronaut.scad
// =====================================================================

part = "assembly"; // [assembly, helmet_inside, legs, torso, deck, arm_left, arm_right, backpack, platform, helmet_front, helmet_back, cam_holder]

pan_angle  = 20;   // assembly preview only
tilt_angle = -15;  // assembly preview only

$fn = 64;
eps = 0.01;

// ---------------- helmet ----------------
helmet_r    = 50;              // outer radius (100 mm helmet)
helmet_wall = 2;
helmet_ri   = helmet_r - helmet_wall;
helmet_cut  = -32;             // helmet-local Z where the sphere is cut flat
visor_r     = 38;              // visor opening half-width
visor_ratio = 0.78;            // visor height / width
visor_z     = 4;               // visor opening center height

// ---------------- torso ----------------
torso_w    = 76;
torso_d    = 60;
torso_z0   = 60;
torso_h    = 72;
torso_r    = 12;
torso_wall = 2.5;
torso_top  = torso_z0 + torso_h;               // 132

collar_od  = 56;
collar_id  = 50;
deck_t     = 3;
deck_d     = collar_id - 0.6;
deck_bot   = torso_top - 0.5;                  // deck rests on the shoulder slab
deck_top   = deck_bot + deck_t;
collar_top = deck_top + 8.6;

// ---------------- helmet platform ----------------
plat_t      = 3;
plat_d      = 80;
plat_bottom = deck_top + 9;                    // sits on the pan servo horn
helmet_z    = plat_bottom + plat_t - helmet_cut;

// ---------------- SG90 micro servo ----------------
sg_l         = 22.5;   // body length
sg_w         = 12.2;   // body width
sg_h         = 22.7;   // body height (without shaft)
sg_tab_l     = 32.3;
sg_tab_t     = 2.5;
sg_tab_z     = 15.9;   // tab underside measured from body bottom
sg_shaft_off = 5.9;    // shaft axis from the gear-end of the body
sg_pitch     = 27.5;   // mounting hole pitch
sg_shaft_h   = 4;

// ---------------- tilt mechanism (helmet-local) ----------------
piv_y       = -15;     // tilt axis (parallel to X) position
piv_z       = 0;
tilt_body_x = -12;     // X of the tilt servo body top (shaft side)

// ---------------- camera ----------------
cam_sq     = 8.6;      // OV2640 lens-holder square base (measure yours!)
lens_d     = 7.4;      // lens barrel hole
brd_y      = 16;       // ESP32-CAM board front face (Y)
brd_w      = 27;
brd_h      = 40.5;
brd_t      = 1.6;

// =====================================================================
//  helpers
// =====================================================================
module rounded_box(w, d, h, r) {
    // centered in X/Y, flat bottom at z=0, rounded top edges/corners
    hull() for (sx = [-1, 1], sy = [-1, 1]) {
        translate([sx * (w/2 - r), sy * (d/2 - r), 0]) cylinder(r = r, h = eps);
        translate([sx * (w/2 - r), sy * (d/2 - r), h - r]) sphere(r = r);
    }
}

module rounded_block(w, d, h, r) {
    // fully rounded vertical edges, flat top/bottom; centered X/Y
    hull() for (sx = [-1, 1], sy = [-1, 1])
        translate([sx * (w/2 - r), sy * (d/2 - r), 0]) cylinder(r = r, h = h);
}

// SG90 dummy: shaft axis = +Z through origin, body top at z = 0
module sg90() {
    cx = sg_l/2 - sg_shaft_off;
    color("DodgerBlue") {
        translate([-sg_shaft_off, -sg_w/2, -sg_h]) cube([sg_l, sg_w, sg_h]);
        translate([cx - sg_tab_l/2, -sg_w/2, -sg_h + sg_tab_z]) cube([sg_tab_l, sg_w, sg_tab_t]);
    }
    color("White") {
        cylinder(d = 4.8, h = sg_shaft_h);
        // single-arm horn
        translate([0, 0, sg_shaft_h - 1.5]) hull() { cylinder(d = 7, h = 1.5); translate([14, 0, 0]) cylinder(d = 4, h = 1.5); }
    }
}

// SG90 mounting cut-out: body opening + two screw pilot holes, along local Z
module sg90_cut() {
    cx = sg_l/2 - sg_shaft_off;
    translate([-sg_shaft_off - 0.3, -sg_w/2 - 0.3, -sg_h - 5]) cube([sg_l + 0.6, sg_w + 0.6, sg_h + 10]);
    for (s = [-1, 1]) translate([cx + s * sg_pitch/2, 0, -sg_h - 5]) cylinder(d = 1.8, h = sg_h + 10, $fn = 16);
}

// =====================================================================
//  LEGS + BASE   (print as is)
// =====================================================================
module legs() {
    // base plate
    hull() for (sx = [-1, 1], sy = [-1, 1]) translate([sx * 40, sy * 28, 0]) cylinder(r = 8, h = 4);
    for (s = [-1, 1]) translate([s * 17, 0, 0]) {
        // boot
        hull() {
            translate([0, -9, 4]) scale([1, 1.25, 1]) cylinder(d = 22, h = 2);
            translate([0, 0, 4]) cylinder(d = 26, h = 2);
            translate([0, -2, 16]) cylinder(d = 25, h = 6);
        }
        // leg
        translate([0, 0, 16]) cylinder(d = 24, h = torso_z0 - 16);
        // knee band and boot cuff
        translate([0, 0, 38]) cylinder(d = 26.5, h = 4);
        translate([0, 0, 22]) cylinder(d = 26.5, h = 3);
        // peg into the torso
        translate([0, 0, torso_z0 - eps]) cylinder(d = 10, h = 4);
    }
}

// =====================================================================
//  TORSO   (print upright, no supports)
// =====================================================================
module torso_outer() { translate([0, 0, torso_z0]) rounded_box(torso_w, torso_d, torso_h, torso_r); }

module torso() {
    difference() {
        union() {
            torso_outer();
            // neck collar
            translate([0, 0, torso_top - 8]) cylinder(d = collar_od, h = collar_top - torso_top + 8, $fn = 96);
            // chest control box with 45 deg chamfered underside
            hull() {
                translate([-17, -torso_d/2 - 6, 96]) cube([34, 7, 16]);
                translate([-17, -torso_d/2 - 0.5, 90]) cube([34, 1, 1]);
            }
            for (x = [-10, 0, 10]) translate([x, -torso_d/2 - 6 + eps, 104]) rotate([90, 0, 0]) cylinder(d = 5, h = 2, $fn = 24);
        }
        // hollow inside
        translate([0, 0, torso_z0 + 3])
            rounded_box(torso_w - 2*torso_wall, torso_d - 2*torso_wall, torso_h - 3 - torso_wall, torso_r - torso_wall);
        // deck seat and opening through the shoulder slab
        translate([0, 0, deck_bot]) cylinder(d = collar_id, h = 50, $fn = 96);
        translate([0, 0, deck_bot - 10]) cylinder(d = 44, h = 11, $fn = 96);
        // anti-rotation key slot for the deck
        translate([-collar_id/2 - 2, -2.2, deck_bot]) cube([4, 4.4, 50]);
        // window to the backpack
        translate([-18, torso_d/2 - 5, 74]) cube([36, 10, 50]);
        // leg pegs
        for (s = [-1, 1]) translate([s * 17, 0, torso_z0 - 1]) cylinder(d = 10.4, h = 6);
        // arm pins (1.75 mm filament)
        for (s = [-1, 1]) translate([s * torso_w/2, 0, 114]) rotate([0, 90, 0]) cylinder(d = 2, h = 12, center = true, $fn = 16);
    }
}

// =====================================================================
//  SERVO DECK   (drops into the collar, holds the PAN servo)
// =====================================================================
module deck() {
    difference() {
        union() {
            cylinder(d = deck_d, h = deck_t, $fn = 96);
            translate([-collar_id/2 - 1.6, -1.9, 0]) cube([3, 3.8, deck_t]);   // key
        }
        translate([0, 0, deck_t]) sg90_cut();
        // cable pass-through slot (arc, back-left)
        for (a = [110:5:160]) rotate([0, 0, a]) translate([18, 0, -1]) cylinder(d = 7, h = deck_t + 2, $fn = 20);
    }
}

// =====================================================================
//  ARMS   (print on the flat shoulder face, light supports)
// =====================================================================
module arm_right() {
    S = [41, 0, 116]; E = [47, -4, 92]; G = [45, -13, 73];
    difference() {
        intersection() {
            union() {
                hull() { translate(S) sphere(r = 11); translate(E) sphere(r = 8.5); }
                hull() { translate(E) sphere(r = 8.5); translate(G) sphere(r = 8); }
                translate(G) sphere(r = 10);          // glove
                translate(E) sphere(r = 9.5);         // elbow joint ring
                translate(G + [0, 0, 8]) rotate([0, 0, 0]) cylinder(d = 19, h = 4, center = true); // glove cuff
            }
            translate([torso_w/2, -50, 0]) cube([40, 100, 200]);
        }
        translate([torso_w/2 - 1, 0, 114]) rotate([0, 90, 0]) cylinder(d = 2, h = 9, $fn = 16);
    }
}

module arm_left() { mirror([1, 0, 0]) arm_right(); }

// =====================================================================
//  BACKPACK (life-support pack = power box)   (print back face down)
// =====================================================================
bp_w = 52; bp_y0 = 18; bp_y1 = 58; bp_z0 = 58; bp_h = 84;

module backpack() {
    difference() {
        translate([0, (bp_y0 + bp_y1)/2, bp_z0]) rounded_block(bp_w, bp_y1 - bp_y0, bp_h, 5);
        // inner cavity, open towards the torso
        translate([0, (bp_y0 + bp_y1)/2 - 2, bp_z0 + 2]) rounded_block(bp_w - 4, bp_y1 - bp_y0, bp_h - 4, 3);
        // conform to the torso back
        minkowski() { torso_outer(); sphere(r = 0.2, $fn = 8); }
        // USB-C breakout slot + switch slot in the bottom
        translate([-14, 44, bp_z0 - 1]) cube([10, 5, 4]);
        translate([6, 42, bp_z0 - 1]) cube([9, 5, 4]);
    }
    // decorative hose stubs on the side
    for (s = [-1, 1]) translate([s * bp_w/2, 40, 120]) rotate([0, s * 90, 0]) cylinder(d = 8, h = 3, $fn = 24);
}

// =====================================================================
//  HELMET PLATFORM  (helmet-local coords; print flange down)
//  carries: tilt servo tower, camera pivot upright, ESP32-CAM rails,
//  screw bosses for both helmet halves, pan-horn pocket underneath.
// =====================================================================
module platform() {
    z0 = helmet_cut;
    difference() {
        union() {
            translate([0, 0, z0 - plat_t]) cylinder(d = plat_d, h = plat_t, $fn = 128);
            // shell screw bosses (follow the inside of the sphere)
            for (a = [45, 135, 225, 315]) intersection() {
                sphere(r = helmet_ri - 0.3, $fn = 128);
                rotate([0, 0, a]) translate([29, -5, z0 - eps]) cube([12, 10, 9]);
            }
            // tilt servo tower (servo tabs rest on its -X face)
            translate([tilt_body_x - 6.8, piv_y - 12, z0 - eps]) cube([2.5, 24, 12 - z0]);
            // pivot upright for the camera holder
            hull() {
                translate([10, piv_y - 7, z0 - eps]) cube([2.5, 14, 1]);
                translate([10, piv_y, piv_z]) rotate([0, 90, 0]) cylinder(r = 5, h = 2.5);
            }
            // ESP32-CAM rails
            for (s = [-1, 1]) {
                translate([s > 0 ? 12.9 : -16, brd_y + brd_t, z0 - eps]) cube([3.1, 1.2, 26]);          // back lip
                translate([s > 0 ? 10.5 : -16, brd_y - 2.5, z0 - eps]) cube([5.5, 2.5, 26]);            // front lip
                translate([s > 0 ? 13.9 : -16, brd_y - eps, z0 - eps]) cube([2.1, brd_t + 2*eps, 26]);  // side
            }
        }
        // tilt servo cut-out + screw holes in the tower
        translate([tilt_body_x, piv_y, piv_z]) rotate([0, 90, 0]) sg90_cut();
        // pivot screw (M2)
        translate([5, piv_y, piv_z]) rotate([0, 90, 0]) cylinder(d = 2.3, h = 20, $fn = 16);
        // board slot clearance (board slides in from the top)
        translate([-brd_w/2 - 0.3, brd_y - 0.15, z0]) cube([brd_w + 0.6, brd_t + 0.3, 40]);
        // shell screw pilot holes (M2 self-tapping)
        for (a = [45, 135, 225, 315]) rotate([0, 0, a]) translate([25, 0, z0 + 4.5]) rotate([0, 90, 0]) cylinder(d = 1.7, h = 30, $fn = 16);
        // cable hole to the torso
        translate([-24, 6, z0 - 10]) cylinder(d = 9, h = 20, $fn = 32);
        // pan servo horn pocket (double-arm horn) + access hole for the horn screw
        translate([0, 0, z0 - plat_t - eps]) for (r = [0, 90]) rotate([0, 0, r]) hull() {
            cylinder(d = 7.5, h = 2);
            for (s = [-1, 1]) translate([s * 16, 0, 0]) cylinder(d = 4.5, h = 2, $fn = 24);
        }
        translate([0, 0, z0 - 10]) cylinder(d = 6, h = 20, $fn = 24);
        // horn screw pilot holes
        for (s = [-1, 1]) translate([s * 10, 0, z0 - 10]) cylinder(d = 1.3, h = 20, $fn = 12);
    }
}

// =====================================================================
//  CAMERA HOLDER   (pivot at origin, tilt axis = X, lens looks to -Y)
// =====================================================================
module cam_holder() {
    difference() {
        union() {
            // front plate + lens pocket block
            translate([-7.5, -7, -7.5]) cube([15, 5.6, 15]);
            // left arm (servo horn side)
            hull() {
                translate([-9.5, -7, -6]) cube([2.1, 5.6, 12]);
                translate([-9.5, 0, 0]) rotate([0, 90, 0]) cylinder(r = 6, h = 2.1);
                translate([-9.5, 14, 0]) rotate([0, 90, 0]) cylinder(r = 3.5, h = 2.1);
            }
            // right arm (pivot side)
            hull() {
                translate([7.4, -7, -6]) cube([2.1, 5.6, 12]);
                translate([7.4, 0, 0]) rotate([0, 90, 0]) cylinder(r = 5, h = 2.1);
            }
        }
        // lens hole through the front plate
        translate([0, -8, 0]) rotate([-90, 0, 0]) cylinder(d = lens_d, h = 5);
        // pocket for the lens-holder base, open to the back and to the bottom (flex cable)
        translate([-cam_sq/2, -5.4, -cam_sq/2]) cube([cam_sq, 10, cam_sq]);
        translate([-cam_sq/2, -5.4, -10]) cube([cam_sq, 10, 10]);
        // servo horn recess on the outer face of the left arm
        translate([-9.5 - eps, 0, 0]) rotate([0, 90, 0]) hull() {
            cylinder(d = 7.6, h = 1.3);
            translate([0, 14, 0]) cylinder(d = 4.6, h = 1.3, $fn = 24);
        }
        translate([-12, 0, 0]) rotate([0, 90, 0]) cylinder(d = 2.5, h = 5, $fn = 16);
        // pivot pilot hole (M2 self-tapping)
        translate([6, 0, 0]) rotate([0, 90, 0]) cylinder(d = 1.7, h = 5, $fn = 16);
    }
}

// =====================================================================
//  HELMET SHELL halves (helmet-local; split on the Y = 0 plane)
// =====================================================================
module visor_shape(grow = 0) {
    translate([0, 0, visor_z]) rotate([90, 0, 0]) scale([1, visor_ratio, 1])
        cylinder(r = visor_r + grow, h = 60, $fn = 128);
}

module helmet_half(front = true) {
    difference() {
        union() {
            sphere(r = helmet_r, $fn = 128);
            if (front) {
                // visor rim
                intersection() {
                    sphere(r = helmet_r + 1.5, $fn = 128);
                    difference() { visor_shape(4); visor_shape(0); }
                }
                // helmet lamps (box faces lie on the seam -> print without supports)
                for (s = [-1, 1]) translate([s > 0 ? helmet_r - 4 : -helmet_r - 4, -11, 6]) cube([8, 11, 12]);
            }
            // seam magnet bosses
            for (s = [-1, 1]) intersection() {
                sphere(r = helmet_r - 0.5, $fn = 128);
                translate([s * 37 - 6, front ? -5 : 0, 20]) cube([12, 5, 10]);
            }
        }
        // hollow, but keep the magnet bosses
        difference() {
            sphere(r = helmet_ri, $fn = 128);
            for (s = [-1, 1]) translate([s * 37 - 6, -5, 20]) cube([12, 10, 10]);
        }
        // flat bottom
        translate([-100, -100, helmet_cut - 100]) cube([200, 200, 100]);
        // keep one half
        if (front) translate([-100, 0, -100]) cube([200, 100, 200]);
        else       translate([-100, -100, -100]) cube([200, 100, 200]);
        // visor opening
        if (front) visor_shape(0);
        // lamp lenses
        if (front) for (s = [-1, 1]) translate([s * helmet_r, -11 - eps, 12]) rotate([-90, 0, 0]) cylinder(d = 7, h = 1, $fn = 32);
        // seam magnets 6x2 mm
        for (s = [-1, 1]) translate([s * 37, 0, 25]) rotate([90, 0, 0]) cylinder(d = 6.2, h = 4.4, center = true, $fn = 32);
        // screw holes into the platform bosses
        for (a = [45, 135, 225, 315]) rotate([0, 0, a]) translate([30, 0, helmet_cut + 4.5]) rotate([0, 90, 0]) cylinder(d = 2.4, h = 30, $fn = 16);
        // cooling vents (back)
        if (!front) for (i = [-2:2]) rotate([0, 0, 90 + i * 14]) translate([helmet_r - 1, 0, -18]) cube([6, 3, 14], center = true);
    }
}

// =====================================================================
//  ELECTRONICS dummies (preview only)
// =====================================================================
module esp32cam_dummy() {
    color("DarkGreen") translate([-brd_w/2, brd_y, helmet_cut]) cube([brd_w, brd_t, brd_h]);
    color("Silver")    translate([-9, brd_y - 3, helmet_cut + 13]) cube([18, 3, 25]);
}

module ov2640_dummy() {
    color("DimGray") {
        translate([-cam_sq/2 + 0.2, -5.2, -cam_sq/2 + 0.2]) cube([cam_sq - 0.4, 3.8, cam_sq - 0.4]);
        translate([0, -5.2, 0]) rotate([90, 0, 0]) cylinder(d = lens_d - 0.4, h = 4);
    }
    color("Orange") translate([-4, -1.4, -14]) cube([8, 0.3, 10]);
}

// =====================================================================
//  ASSEMBLY
// =====================================================================
module helmet_assembly() {
    color("WhiteSmoke") platform();
    translate([tilt_body_x, piv_y, piv_z]) rotate([0, 90, 0]) sg90();
    translate([0, piv_y, piv_z]) rotate([tilt_angle, 0, 0]) {
        color("Gold") cam_holder();
        ov2640_dummy();
    }
    esp32cam_dummy();
    color("White", 0.9) helmet_half(false);
    if (part != "helmet_inside") color("White", 0.35) helmet_half(true);
}

module assembly() {
    color("Gainsboro") legs();
    color("White") torso();
    color("Gold") translate([0, 0, deck_bot]) deck();
    translate([0, 0, deck_top + 6.8]) sg90();
    color("White") { arm_right(); arm_left(); }
    color("LightGray") backpack();
    translate([0, 0, helmet_z]) rotate([0, 0, pan_angle]) helmet_assembly();
}

// =====================================================================
//  PART SELECTOR (each part placed in its printing orientation)
// =====================================================================
if (part == "assembly")     assembly();
if (part == "helmet_inside") translate([0, 0, -helmet_z]) assembly();   // preview with the front shell removed
if (part == "legs")         legs();
if (part == "torso")        translate([0, 0, -torso_z0]) torso();
if (part == "deck")         deck();
if (part == "arm_right")    rotate([0, -90, 0]) translate([-torso_w/2, 0, 0]) arm_right();
if (part == "arm_left")     rotate([0, 90, 0]) translate([torso_w/2, 0, 0]) arm_left();
if (part == "backpack")     translate([0, 0, bp_y1]) rotate([-90, 0, 0]) translate([0, 0, 0]) backpack();
if (part == "platform")     translate([0, 0, -helmet_cut + plat_t]) platform();
if (part == "helmet_front") rotate([-90, 0, 0]) helmet_half(true);
if (part == "helmet_back")  rotate([90, 0, 0]) helmet_half(false);
if (part == "cam_holder")   translate([0, 0, 7]) rotate([90, 0, 0]) cam_holder();
