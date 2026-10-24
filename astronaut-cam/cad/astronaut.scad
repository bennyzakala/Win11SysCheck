// =====================================================================
//  Astronaut Cam - printable "galaxy projector" style astronaut
//  with a Wi-Fi pan/tilt camera inside the helmet.
//
//  Proportions follow the common astronaut galaxy-projector toy
//  (about 23 x 12 x 11 cm, ~10 cm helmet): the helmet is roughly half of
//  the figure, the body is short and compact, legs are short.
//
//  - Pan  : SG90 servo inside the torso turns the whole helmet (0-180 deg)
//  - Tilt : SG90 servo inside the helmet nods the camera behind the visor
//  - Brain: ESP32-CAM (AI-Thinker) + OV2640 with a 75 mm flex cable
//
//  Units: mm. Front of the astronaut is -Y, up is +Z.
//  Export a single part:  openscad -D 'part="torso"' -o torso.stl astronaut.scad
// =====================================================================

part = "assembly"; // [assembly, helmet_inside, moon_base, legs, torso, deck, arm_left, arm_right, backpack, platform, helmet_front, helmet_back, cam_holder]

pan_angle  = 20;   // assembly preview only
tilt_angle = -10;  // assembly preview only

$fn = 72;          // smooth curved surfaces (raise for even smoother prints)
eps = 0.01;
gold_overlay = (part == "assembly" || part == "helmet_inside");   // preview: draw gold parts separately

// ---------------- helmet ----------------
helmet_r    = 50;              // outer radius (100 mm helmet)
helmet_wall = 2;
helmet_ri   = helmet_r - helmet_wall;
helmet_cut  = -32;             // helmet-local Z where the sphere is cut flat
visor_r     = 37;              // visor opening half-width
visor_ratio = 0.84;            // visor height / width
visor_z     = 2;               // visor opening center height

// ---------------- body (all heights from the table) ----------------
base_h     = 16;               // moon base top surface
boot_top   = base_h + 13;
torso_z0   = 56;               // crotch: legs / torso split
torso_top  = 103;              // shoulder line
torso_w    = 56;
torso_d_top = 50;              // chest depth (holds the neck ring)
torso_d_bot = 44;              // hip depth
torso_rc   = 12;               // corner radius
torso_wall = 2.4;
shoulder_x = 27.5;             // flat arm pads at +-shoulder_x
leg_x      = 13;               // leg centre lines at +-leg_x

collar_od  = 52;               // neck ring (bearing for the helmet)
collar_id  = 46;
deck_t     = 3;
deck_d     = collar_id - 0.6;
deck_bot   = torso_top - 9;    // deck sits low inside the chest -> short neck
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
tab_x       = tilt_body_x - sg_h + sg_tab_z;   // X of the tilt servo tab underside

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
module ell(c, r) { translate(c) scale(r) sphere(r = 1, $fn = 64); }

module rounded_block(w, d, h, r) {
    // rounded vertical edges, flat top/bottom; centered X/Y
    hull() for (sx = [-1, 1], sy = [-1, 1])
        translate([sx * (w/2 - r), sy * (d/2 - r), 0]) cylinder(r = r, h = h);
}

module rounded_cube(size, r) {
    // fully rounded box, min corner at origin
    hull() for (x = [r, size[0] - r], y = [r, size[1] - r], z = [r, size[2] - r])
        translate([x, y, z]) sphere(r = r, $fn = 24);
}

// rotate children so that +Z points from a to b, origin at a
module along(a, b) {
    v = b - a;
    translate(a) rotate([0, acos(v[2] / norm(v)), atan2(v[1], v[0])]) children();
}

// smooth radius profile: pts = [[z, r], ...] (z ascending), cosine-eased between points
function _seg(pts, z, i = 0) = (i >= len(pts) - 2 || z < pts[i + 1][0]) ? i : _seg(pts, z, i + 1);
function rprof(pts, z) =
    let(i = _seg(pts, z), a = pts[i], b = pts[i + 1],
        t = min(1, max(0, (z - a[0]) / (b[0] - a[0]))), s = (1 - cos(180 * t)) / 2)
    a[1] + (b[1] - a[1]) * s;

// lathe-turned body of revolution following a radius profile (very smooth)
module lathe(pts, step = 0.35) {
    z0 = pts[0][0]; z1 = pts[len(pts) - 1][0];
    rotate_extrude($fn = 96)
        polygon(concat([[0, z0]], [for (z = [z0:step:z1]) [rprof(pts, z), z]], [[rprof(pts, z1), z1], [0, z1]]));
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
//  MOON BASE   (print in grey)
// =====================================================================
craters = [[-28, -22, 6], [27, -26, 4.5], [31, 12, 5.5], [-30, 20, 4.5], [2, -38, 3.5], [-9, 36, 3],
           [16, 35, 4], [-38, -2, 2.6], [12, -36, 2.2], [37, -8, 2.5], [-20, -36, 2.2], [24, 25, 2],
           [-24, 33, 2], [0, 28, 2.4], [-36, -14, 1.8], [36, 22, 1.8]];

module moon_rock() {
    hull() {
        scale([44, 52, 1]) cylinder(r = 1, h = 2, $fn = 128);
        ell([-8, 4, 7],  [36, 44, 9]);
        ell([10, -6, 8], [33, 40, 8]);
        ell([4, 12, 9],  [30, 34, 7]);
    }
}

module moon_base() {
    difference() {
        intersection() { moon_rock(); cylinder(r = 60, h = base_h, $fn = 64); }
        for (c = craters) translate([c[0], c[1], base_h + c[2] * 0.62]) sphere(r = c[2], $fn = 40);
    }
    // soft crater rims
    for (c = craters) intersection() {
        translate([c[0], c[1], base_h - 0.6]) rotate_extrude($fn = 48) translate([c[2] * 0.82 + 0.6, 0]) scale([1, 1.1]) circle(r = 0.9, $fn = 16);
        moon_rock();
        translate([c[0], c[1], base_h - 1]) cylinder(r = c[2] + 3, h = 3);
    }
    // pegs for the boots
    for (s = [-1, 1]) translate([s * leg_x, -2, base_h - eps]) cylinder(d = 6.6, h = 4, $fn = 32);
}

// =====================================================================
//  LEGS + BOOTS   (two pieces in one file, print upright)
// =====================================================================
// smooth leg: ankle -> calf -> soft knee crease -> thigh
leg_prof = [[boot_top - 3, 8.6], [boot_top + 1, 8.8], [boot_top + 6, 10.2], [boot_top + 11.5, 9.9],
            [boot_top + 14, 10.4], [boot_top + 20, 11], [torso_z0 + 0.01, 11.4]];

module boot() {
    hull() {
        translate([0, -5, base_h]) scale([10.5, 16.5, 1]) cylinder(r = 1, h = 2.5, $fn = 96);  // sole
        ell([0, -12, base_h + 5.5], [9.6, 9.5, 5.5]);                                         // toe
        ell([0, 1.5, base_h + 8], [10.4, 10.8, 6]);                                            // heel / ankle
        translate([0, 0, boot_top - 1]) cylinder(r = 9.6, h = 1, $fn = 96);                    // cuff top
    }
}

module boot_details() {
    // sole welt
    translate([0, -5, base_h]) scale([11.1, 17.1, 1]) cylinder(r = 1, h = 2.2, $fn = 96);
    // strap across the instep
    intersection() {
        hull() {
            translate([0, -5, base_h]) scale([11.2, 17.2, 1]) cylinder(r = 1, h = 2.5, $fn = 96);
            ell([0, -12, base_h + 5.5], [10.2, 10.1, 6.1]);
            ell([0, 1.5, base_h + 8], [11, 11.4, 6.6]);
        }
        translate([-15, -9, base_h + 2]) rotate([-35, 0, 0]) cube([30, 3.2, 20]);
    }
    // cuff ring
    translate([0, 0, boot_top - 1.6]) rotate_extrude($fn = 96) translate([9.6, 0]) circle(r = 1, $fn = 24);
}

module knee_pads() {   // two gold bars on the front of the knee (paint or filament swap)
    for (z = [boot_top + 10.4, boot_top + 13]) intersection() {
        translate([0, 0, z]) cylinder(r = 11.1, h = 1.8, $fn = 96);
        translate([-8, -20, z - 1]) cube([16, 20, 4]);
    }
}

module leg_piece() {
    difference() {
        union() {
            lathe(leg_prof);
            boot();
            boot_details();
            if (!gold_overlay) knee_pads();
        }
        translate([0, -2, base_h - 1]) cylinder(d = 7, h = 5, $fn = 32);       // base peg hole
    }
    translate([0, 0, torso_z0 - eps]) cylinder(d = 8, h = 4, $fn = 32);          // peg into the torso
}

module legs() { for (s = [-1, 1]) translate([s * leg_x, 0, 0]) leg_piece(); }

// =====================================================================
//  TORSO   (print upright, no supports)
// =====================================================================
// compact rounded body: deep chest on top (holds the neck ring), slightly narrower hips
module torso_core(o = 0) {
    r = torso_rc - o; h = torso_top - torso_z0;
    hull() for (sx = [-1, 1], sy = [-1, 1]) {
        translate([sx * (torso_w/2 - torso_rc), sy * (torso_d_bot/2 - torso_rc), torso_z0]) scale([1, 1, 0.01]) cylinder(r = r, h = 1);
        translate([sx * (torso_w/2 - torso_rc), sy * (torso_d_bot/2 - torso_rc), torso_z0 + 5]) sphere(r = r);
        translate([sx * (torso_w/2 - torso_rc), sy * (torso_d_top/2 - torso_rc), torso_top - torso_rc]) sphere(r = r);
        ell([0, 0, 80], [torso_w/2 + 1.4 - o, torso_d_bot/2 + 1.6 - o, 19]);   // soft belly / side bulge
    }
}

module torso_clip() { translate([-shoulder_x, -60, torso_z0]) cube([2 * shoulder_x, 120, 120]); }

module torso_skin() {
    intersection() {
        union() {
            torso_core();
            for (s = [-1, 1]) ell([s * (shoulder_x - 3), 0, 93], [4, 8.5, 8.5]);   // shoulder pads under the arms
        }
        torso_clip();
    }
}

module chest_panel() {
    fy = -torso_d_top/2 + 0.4;         // front surface
    // main plate with 45 deg chamfered underside
    hull() {
        translate([-17, fy - 3, 77]) rounded_cube([34, 3.6, 16], 0.8);
        translate([-17, fy, 72.5]) cube([34, 0.5, 1]);
    }
    translate([-15, fy - 4.4, 89]) rounded_cube([30, 2, 3], 0.6);                      // top rail
    translate([1, fy - 5.6, 82]) rounded_cube([11, 3, 9], 0.6);                         // raised block
    for (x = [3, 6.3, 9.6]) translate([x - 0.8, fy - 6.6, 83]) rounded_cube([1.6, 1.4, 8.5], 0.4);  // bars
    translate([-8, fy - 2.6, 86]) rotate([90, 0, 0])                                    // triangle mark
        linear_extrude(1.6) polygon([[-3.2, -2.6], [3.2, -2.6], [0, 2.8]]);
    for (i = [0:5]) translate([-14.6 + i * 5, fy - 4.6, 78.2]) rounded_cube([4, 1.8, 3], 0.5);  // keypad
}

module belt() {
    intersection() {
        hull() torso_core(-1.3);
        translate([-50, -50, torso_z0 + 4]) cube([100, 100, 6.5]);
    }
    translate([-8, -torso_d_bot/2 - 2.6, torso_z0 + 3.4]) rounded_cube([16, 3, 7.7], 1);   // buckle
}

module torso() {
    difference() {
        union() {
            torso_skin();
            intersection() { union() { chest_panel(); belt(); } torso_clip(); }
            // neck ring (bearing for the helmet platform)
            translate([0, 0, torso_top - 6]) cylinder(d = collar_od, h = collar_top - torso_top + 6, $fn = 128);
        }
        // soft suit seam under the chest
        intersection() {
            difference() { torso_core(-1); torso_core(0.7); }
            translate([-50, -50, 71]) cube([100, 100, 0.9]);
        }
        // hollow inside, 45 deg conical ceiling -> no supports
        intersection() {
            torso_core(torso_wall);
            translate([-shoulder_x + torso_wall, -60, torso_z0 + 3]) cube([2 * (shoulder_x - torso_wall), 120, deck_bot - 4 - torso_z0 - 3]);
            union() {
                translate([0, 0, deck_bot - 4 - 30]) cylinder(r1 = 50, r2 = 20, h = 30, $fn = 96);
                translate([0, 0, torso_z0]) cylinder(r = 50, h = deck_bot - 4 - 30 - torso_z0 + eps, $fn = 96);
            }
        }
        // deck seat and opening through the shoulder slab
        translate([0, 0, deck_bot]) cylinder(d = collar_id, h = 50, $fn = 128);
        translate([0, 0, deck_bot - 10]) cylinder(d = 40, h = 11, $fn = 96);
        // anti-rotation key slot for the deck
        translate([-collar_id/2 - 2, -2.2, deck_bot]) cube([4, 4.4, 50]);
        // window to the backpack
        translate([-14, 8, 62]) cube([28, 30, 26]);
        // leg pegs
        for (s = [-1, 1]) translate([s * leg_x, 0, torso_z0 - 1]) cylinder(d = 8.4, h = 6, $fn = 32);
        // arm pins (1.75 mm filament)
        for (s = [-1, 1]) translate([s * shoulder_x, 0, 93]) rotate([0, 90, 0]) cylinder(d = 2, h = 12, center = true, $fn = 16);
    }
}

// =====================================================================
//  SERVO DECK   (drops into the neck ring, holds the PAN servo)
// =====================================================================
module deck() {
    difference() {
        union() {
            cylinder(d = deck_d, h = deck_t, $fn = 128);
            translate([-collar_id/2 - 1.6, -1.9, 0]) cube([3, 3.8, deck_t]);   // key
        }
        translate([0, 0, deck_t]) sg90_cut();
        // cable pass-through slot (arc, back-left)
        for (a = [110:5:160]) rotate([0, 0, a]) translate([17, 0, -1]) cylinder(d = 7, h = deck_t + 2, $fn = 20);
    }
}

// =====================================================================
//  ARMS   (smooth tapered limbs, slightly bent; print on the flat shoulder face with supports)
// =====================================================================
arm_S = [32, 0, 93];        // shoulder
arm_E = [35.5, -1.5, 75];   // elbow
arm_W = [36.5, -7.5, 63];   // wrist
arm_F = [37, -9.5, 57.5];   // fist centre

module shoulder_patch() {   // gold patch on the outside of the shoulder (paint)
    intersection() {
        difference() { ell(arm_S, [9.8, 9.8, 9.8]); ell(arm_S, [8.6, 8.6, 8.6]); }
        translate([arm_S[0] + 3.5, -5, arm_S[2] - 1]) cube([10, 10, 7.5]);
    }
}

module fist() {
    hull() {
        ell(arm_F, [6.6, 7.4, 6.8]);
        ell(arm_F + [-0.5, -1.8, -1.5], [6, 6.4, 5.8]);
    }
    hull() { ell(arm_F + [-5, -4.5, 2], [2.7, 2.7, 3.2]); ell(arm_F + [-5.5, -6.5, -1.5], [2.5, 2.5, 2.6]); }   // thumb
}

module arm_right() {
    difference() {
        intersection() {
            union() {
                ell(arm_S, [9.4, 9.6, 9.4]);                                          // shoulder ball
                hull() { ell(arm_S + [0.6, 0, -4], [8.2, 8.4, 6]); ell((arm_S + arm_E) / 2 + [0.3, -0.6, 0], [8, 8.3, 5]); translate(arm_E) sphere(r = 7); }   // upper arm
                translate(arm_E) sphere(r = 7.2);                                      // elbow
                hull() { translate(arm_E) sphere(r = 6.8); translate(arm_W) sphere(r = 5.9); }   // forearm
                along(arm_W, arm_E) translate([0, 0, 0.5]) rotate_extrude($fn = 64)    // glove cuff
                    hull() { translate([5.6, 0]) square([0.1, 3]); translate([6.4, 1.5]) circle(r = 1.2, $fn = 20); }
                fist();
                if (!gold_overlay) shoulder_patch();
            }
            translate([shoulder_x, -60, 0]) cube([60, 120, 200]);
        }
        translate([shoulder_x - 1, 0, 93]) rotate([0, 90, 0]) cylinder(d = 2, h = 9, $fn = 16);
    }
}

module arm_left() { mirror([1, 0, 0]) arm_right(); }

// =====================================================================
//  BACKPACK (power box with two pockets)   (print upright)
// =====================================================================
bp_w = 42; bp_y0 = 12; bp_y1 = 36; bp_z0 = 60; bp_h = 44;

module backpack() {
    difference() {
        union() {
            translate([-bp_w/2, bp_y0, bp_z0]) rounded_cube([bp_w, bp_y1 - bp_y0, bp_h], 4);
            for (s = [-1, 1]) {
                translate([s * 10.5 - 8, bp_y1 - 2, bp_z0 + 3]) rounded_cube([16, 6.5, 19], 2);       // pocket
                translate([s * 10.5 - 8.6, bp_y1 - 2, bp_z0 + 19]) rounded_cube([17.2, 7.3, 6], 2);   // pocket flap
            }
            translate([-bp_w/2 + 4, bp_y1 - 2, bp_z0 + bp_h - 13]) rounded_cube([bp_w - 8, 4.5, 9], 2); // top roll
        }
        // inner cavity, open towards the torso
        translate([-bp_w/2 + 2.2, bp_y0 - 1, bp_z0 + 2.2]) cube([bp_w - 4.4, bp_y1 - bp_y0 - 1.2, bp_h - 4.4]);
        // conform to the torso back and the neck ring
        torso_core(-0.3);
        translate([0, 0, torso_top - 6]) cylinder(d = collar_od + 1, h = 40, $fn = 96);
        // USB-C breakout slot in the bottom
        translate([-5, 24, bp_z0 - 1]) cube([10, 5, 4]);
    }
}

// =====================================================================
//  HELMET PLATFORM  (helmet-local coords; print flange down)
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
            // tilt servo tower (servo tabs rest on its +X face)
            translate([tab_x - 2.5, piv_y - 12, z0 - eps]) cube([2.5, 24, 12 - z0]);
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
        for (s = [-1, 1]) translate([s * 10, 0, z0 - 10]) cylinder(d = 1.3, h = 20, $fn = 12);
    }
}

// =====================================================================
//  CAMERA HOLDER   (pivot at origin, tilt axis = X, lens looks to -Y)
// =====================================================================
module cam_holder() {
    difference() {
        union() {
            translate([-7.5, -7, -7.5]) cube([15, 5.6, 15]);
            hull() {
                translate([-9.5, -7, -6]) cube([2.1, 5.6, 12]);
                translate([-9.5, 0, 0]) rotate([0, 90, 0]) cylinder(r = 6, h = 2.1);
                translate([-9.5, 14, 0]) rotate([0, 90, 0]) cylinder(r = 3.5, h = 2.1);
            }
            hull() {
                translate([7.4, -7, -6]) cube([2.1, 5.6, 12]);
                translate([7.4, 0, 0]) rotate([0, 90, 0]) cylinder(r = 5, h = 2.1);
            }
        }
        translate([0, -8, 0]) rotate([-90, 0, 0]) cylinder(d = lens_d, h = 5);
        translate([-cam_sq/2, -5.4, -cam_sq/2]) cube([cam_sq, 10, cam_sq]);
        translate([-cam_sq/2, -5.4, -10]) cube([cam_sq, 10, 10]);
        translate([-9.5 - eps, 0, 0]) rotate([0, 90, 0]) hull() {
            cylinder(d = 7.6, h = 1.3);
            translate([0, 14, 0]) cylinder(d = 4.6, h = 1.3, $fn = 24);
        }
        translate([-12, 0, 0]) rotate([0, 90, 0]) cylinder(d = 2.5, h = 5, $fn = 16);
        translate([6, 0, 0]) rotate([0, 90, 0]) cylinder(d = 1.7, h = 5, $fn = 16);
    }
}

// =====================================================================
//  HELMET SHELL halves (helmet-local; split on the Y = 0 plane)
//  smooth sphere; the front half carries the visor opening and its rounded bezel
// =====================================================================
module visor_shape(grow = 0) {
    translate([0, 0, visor_z]) rotate([90, 0, 0]) scale([1, visor_ratio, 1])
        cylinder(r = visor_r + grow, h = 60, $fn = 128);
}

// point on the helmet surface (radius rr) above the visor ellipse, angle t
function visor_pt(t, grow, rr) =
    let(x = (visor_r + grow) * cos(t), z = visor_z + (visor_r + grow) * visor_ratio * sin(t))
    [x, -sqrt(max(0, rr * rr - x * x - z * z)), z];

// rounded bead that follows the visor edge on the sphere
module visor_bead(n = 90, br = 2.7) {
    for (i = [0:n - 1]) hull() {
        translate(visor_pt(i * 360 / n, br, helmet_r - 0.6)) sphere(r = br, $fn = 24);
        translate(visor_pt((i + 1) * 360 / n, br, helmet_r - 0.6)) sphere(r = br, $fn = 24);
    }
}

module helmet_half(front = true) {
    difference() {
        union() {
            sphere(r = helmet_r, $fn = 160);
            if (front) visor_bead();                                          // smooth, rounded visor bezel
            for (s = [-1, 1]) intersection() {                                // seam magnet bosses
                sphere(r = helmet_r - 0.5, $fn = 128);
                translate([s * 37 - 6, front ? -5 : 0, 20]) cube([12, 5, 10]);
            }
        }
        difference() {
            sphere(r = helmet_ri, $fn = 128);
            for (s = [-1, 1]) translate([s * 37 - 6, -5, 20]) cube([12, 10, 10]);
        }
        translate([-100, -100, helmet_cut - 100]) cube([200, 200, 100]);
        if (front) translate([-100, 0, -100]) cube([200, 100, 200]);
        else       translate([-100, -100, -100]) cube([200, 100, 200]);
        if (front) visor_shape(0);
        // seam magnets 6x2 mm
        for (s = [-1, 1]) translate([s * 37, 0, 25]) rotate([90, 0, 0]) cylinder(d = 6.2, h = 4.4, center = true, $fn = 32);
        // screw holes into the platform bosses
        for (a = [45, 135, 225, 315]) rotate([0, 0, a]) translate([30, 0, helmet_cut + 4.5]) rotate([0, 90, 0]) cylinder(d = 2.4, h = 30, $fn = 16);
        // cooling vents (back)
        if (!front) for (i = [-2:2]) rotate([0, 0, 90 + i * 14]) translate([helmet_r - 1, 0, -18]) cube([6, 3, 14], center = true);
    }
}

// =====================================================================
//  ELECTRONICS dummies + preview-only colour overlays
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

module tinted_visor() {   // smoked film behind the bezel (preview only)
    color([0.05, 0.06, 0.1], 0.82) intersection() {
        difference() { sphere(r = helmet_ri + 0.6, $fn = 128); sphere(r = helmet_ri + 0.1, $fn = 128); }
        visor_shape(1);
        translate([-100, -100, -100]) cube([200, 100, 200]);
    }
}


module gold_details() {
    color("Goldenrod") {
        for (s = [-1, 1]) translate([s * leg_x, 0, 0]) knee_pads();
        shoulder_patch();
        mirror([1, 0, 0]) shoulder_patch();
    }
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
    color("White") render() helmet_half(false);
    if (part != "helmet_inside") { color("White") render() helmet_half(true); tinted_visor(); }
}

module assembly() {
    color("DimGray") moon_base();
    color("White") legs();
    color("White") torso();
    color("Gold") translate([0, 0, deck_bot]) deck();
    translate([0, 0, deck_top + sg_h - sg_tab_z]) sg90();
    color("White") { arm_right(); arm_left(); }
    color("WhiteSmoke") backpack();
    gold_details();
    translate([0, 0, helmet_z]) rotate([0, 0, pan_angle]) helmet_assembly();
}

// =====================================================================
//  PART SELECTOR (each part placed in its printing orientation)
// =====================================================================
if (part == "assembly")      assembly();
if (part == "helmet_inside") translate([0, 0, -helmet_z]) assembly();   // preview with the front shell removed
if (part == "moon_base")     moon_base();
if (part == "legs")          translate([0, 0, -base_h]) legs();
if (part == "torso")         translate([0, 0, -torso_z0]) torso();
if (part == "deck")          deck();
if (part == "arm_right")     rotate([0, -90, 0]) translate([-shoulder_x, 0, 0]) arm_right();
if (part == "arm_left")      rotate([0, 90, 0]) translate([shoulder_x, 0, 0]) arm_left();
if (part == "backpack")      translate([0, 0, -bp_z0]) backpack();
if (part == "platform")      translate([0, 0, -helmet_cut + plat_t]) platform();
if (part == "helmet_front")  rotate([-90, 0, 0]) helmet_half(true);
if (part == "helmet_back")   rotate([90, 0, 0]) helmet_half(false);
if (part == "cam_holder")    translate([0, 0, 7]) rotate([90, 0, 0]) cam_holder();
