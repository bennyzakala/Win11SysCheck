// =====================================================================
//  Astronaut Cam - printable "galaxy projector" style astronaut
//  with a Wi-Fi pan/tilt camera inside the helmet.
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

$fn = 64;
eps = 0.01;

// ---------------- helmet ----------------
helmet_r    = 50;              // outer radius (100 mm helmet)
helmet_wall = 2;
helmet_ri   = helmet_r - helmet_wall;
helmet_cut  = -32;             // helmet-local Z where the sphere is cut flat
visor_r     = 37;              // visor opening half-width
visor_ratio = 0.84;            // visor height / width
visor_z     = 2;               // visor opening center height

// ---------------- body ----------------
base_h     = 14;               // moon base thickness
torso_z0   = 74;               // legs / torso split
torso_top  = 146;
shoulder_x = 32;               // flat arm pads at +-shoulder_x
torso_wall = 2.5;

collar_od  = 56;               // neck ring
collar_id  = 50;
deck_t     = 3;
deck_d     = collar_id - 0.6;
deck_bot   = torso_top - 0.5;  // deck rests on the shoulder slab
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
module ell(c, r) { translate(c) scale(r) sphere(r = 1, $fn = 40); }

module rounded_block(w, d, h, r) {
    // rounded vertical edges, flat top/bottom; centered X/Y
    hull() for (sx = [-1, 1], sy = [-1, 1])
        translate([sx * (w/2 - r), sy * (d/2 - r), 0]) cylinder(r = r, h = h);
}

module rounded_cube(size, r) {
    // fully rounded box, min corner at origin
    hull() for (x = [r, size[0] - r], y = [r, size[1] - r], z = [r, size[2] - r])
        translate([x, y, z]) sphere(r = r, $fn = 16);
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
//  MOON BASE   (print in grey, as is)
// =====================================================================
craters = [[-38, -16, 7], [34, -22, 5], [38, 16, 6], [-34, 22, 5], [0, -37, 4],
           [-12, 33, 3.5], [18, 34, 4], [-47, 2, 3], [11, -36, 2.5], [47, -3, 3], [-24, -32, 2.5]];

module moon_base() {
    difference() {
        hull() {
            scale([58, 50, 1]) cylinder(r = 1, h = 2, $fn = 96);
            translate([0, 0, 8]) scale([57, 49, 1]) cylinder(r = 1, h = 1, $fn = 96);
            translate([0, 0, base_h - 1]) scale([52, 44, 1]) cylinder(r = 1, h = 1, $fn = 96);
        }
        for (c = craters) translate([c[0], c[1], base_h + c[2] * 0.55]) sphere(r = c[2], $fn = 32);
        // dents around the edge
        for (a = [20, 75, 140, 200, 250, 310]) translate([57 * cos(a), 49 * sin(a), 6]) sphere(r = 3.5, $fn = 24);
    }
    // pegs for the boots
    for (s = [-1, 1]) translate([s * 15, -3, base_h - eps]) cylinder(d = 7.6, h = 5);
    // crater rims
    for (c = craters) translate([c[0], c[1], base_h - 0.5]) difference() {
        cylinder(r = c[2] * 0.84 + 1.2, h = 1.3, $fn = 32);
        translate([0, 0, -1]) cylinder(r = c[2] * 0.84, h = 4, $fn = 32);
    }
}

// =====================================================================
//  LEGS + BOOTS   (two pieces in one file, print upright)
// =====================================================================
module boot_shape() {
    hull() {
        translate([0, -6, base_h]) scale([11, 19, 1]) cylinder(r = 1, h = 3, $fn = 48);   // sole
        ell([0, -15, base_h + 6], [10, 9, 6]);                                           // toe
        ell([0, 0, base_h + 10], [11, 12, 7]);                                           // ankle
    }
}

module boot() {
    difference() {
        union() {
            boot_shape();
            translate([0, -6, base_h]) scale([11.7, 19.7, 1]) cylinder(r = 1, h = 2.2, $fn = 48);    // sole ridge
            intersection() { resize([24, 41, 0]) boot_shape(); translate([-20, -16, 0]) cube([40, 3.5, 60]); }  // strap
        }
        translate([0, -3, base_h - 1]) cylinder(d = 8, h = 6);                                   // peg hole
    }
}

knee_bands = [49.6, 52.8];     // z of the two knee bands (paint gold / filament change)

module leg() {
    translate([0, 0, 28]) scale([1, 1.04, 1]) cylinder(d = 25, h = torso_z0 - 28);
    for (z = [39, 61]) ell([0, 0, z], [13.6, 14.1, 8]);        // puffy segments (shin / thigh)
    ell([0, 0, 71], [14, 14.5, 5]);
    for (z = knee_bands) translate([0, 0, z]) scale([14.2, 14.7, 1]) cylinder(r = 1, h = 1.8, $fn = 48);
}

module legs() {
    for (s = [-1, 1]) translate([s * 15, 0, 0]) {
        intersection() {
            union() { leg(); boot(); }
            translate([-30, -40, 0]) cube([60, 80, torso_z0]);
        }
        translate([0, 0, torso_z0 - eps]) cylinder(d = 8, h = 4);     // peg into the torso
    }
}

// =====================================================================
//  TORSO   (print upright, no supports)
// =====================================================================
module torso_core(o = 0) {
    hull() {
        translate([0, 0, torso_z0]) scale([29 - o, 23 - o, 1]) cylinder(r = 1, h = eps, $fn = 64);
        ell([0, 0, 84],  [29.5 - o, 23.5 - o, 8]);                                  // hips
        ell([0, -1, 122], [32 - o, 25 - o, 14]);                                    // chest
        for (s = [-1, 1]) ell([s * 21, 1, 135], [12.5 - o, 13 - o, 11 - o]);        // shoulders
        ell([0, 1, 139],  [24 - o, 20 - o, 7 - o]);                                 // neck base
    }
}

module torso_clip() { translate([-shoulder_x, -60, torso_z0]) cube([2 * shoulder_x, 120, 120]); }

module chest_panel() {
    hull() {
        translate([-16, -30, 112]) cube([32, 6, 21]);
        translate([-16, -25, 106]) cube([32, 1, 1]);
    }
    translate([-2, -31.5, 123]) cube([16, 2, 10]);                                  // raised right block
    translate([-9, -30 + eps, 129]) rotate([90, 0, 0])                              // triangle mark
        linear_extrude(1.4) polygon([[-4, -3.2], [4, -3.2], [0, 3.2]]);
    for (x = [1.5, 5.5, 9.5]) translate([x, -32.7, 125]) cube([2, 1.3, 7]);         // vertical bars
    for (i = [0:5]) translate([-14.2 + i * 5, -31.2, 115]) cube([3.6, 1.3, 3]);     // keypad
}

module torso() {
    difference() {
        union() {
            intersection() { torso_core(); torso_clip(); }
            chest_panel();
            // belt + buckle
            intersection() { torso_core(-1.6); translate([-50, -50, 94]) cube([100, 100, 7]); }
            translate([-8, -27.6, 93.5]) cube([16, 3, 8]);
            // neck ring
            translate([0, 0, torso_top - 8]) cylinder(d = collar_od, h = collar_top - torso_top + 8, $fn = 96);
        }
        // suit seam grooves
        for (z = [80, 108]) intersection() {
            difference() { torso_core(-1); torso_core(0.8); }
            translate([-50, -50, z]) cube([100, 100, 1.2]);
        }
        // hollow inside, 45 deg conical ceiling -> no supports
        intersection() {
            torso_core(torso_wall);
            translate([-shoulder_x + torso_wall, -60, torso_z0 + 3]) cube([2 * (shoulder_x - torso_wall), 120, deck_bot - 4 - torso_z0 - 3]);
            union() {
                translate([0, 0, deck_bot - 4 - 36]) cylinder(r1 = 58, r2 = 22, h = 36, $fn = 96);
                translate([0, 0, torso_z0]) cylinder(r = 58, h = deck_bot - 4 - 36 - torso_z0 + eps, $fn = 96);
            }
        }
        // deck seat and opening through the shoulder slab
        translate([0, 0, deck_bot]) cylinder(d = collar_id, h = 50, $fn = 96);
        translate([0, 0, deck_bot - 10]) cylinder(d = 44, h = 11, $fn = 96);
        // anti-rotation key slot for the deck
        translate([-collar_id/2 - 2, -2.2, deck_bot]) cube([4, 4.4, 50]);
        // window to the backpack
        translate([-14, 8, 98]) cube([28, 30, 38]);
        // leg pegs
        for (s = [-1, 1]) translate([s * 15, 0, torso_z0 - 1]) cylinder(d = 8.4, h = 6);
        // arm pins (1.75 mm filament)
        for (s = [-1, 1]) translate([s * shoulder_x, 1, 135]) rotate([0, 90, 0]) cylinder(d = 2, h = 12, center = true, $fn = 16);
    }
}

// =====================================================================
//  SERVO DECK   (drops into the neck ring, holds the PAN servo)
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
//  ARMS   (puffy segments; print on the flat shoulder face, with supports)
// =====================================================================
arm_seg = [   // [x, y, z, rx, ry, rz]
    [38,   0,   132, 12.5, 12.5, 12],    // shoulder ball
    [41.5, -1,  117, 10.8, 11.2, 8],     // upper arm
    [43.5, -3,  104, 10.3, 10.8, 7],     // elbow
    [44,  -5.5,  93, 9.8,  10.2, 7],     // forearm
    [43,  -7.5,  84.5, 9.2, 9.6, 5.5],
];

module shoulder_patch() {   // paint gold
    translate([48.8, 0, 133]) rotate([0, 90, 0]) hull()
        for (y = [-4.5, 4.5], z = [-3, 3]) translate([z, y, 0]) cylinder(r = 1.8, h = 2.6, $fn = 16);
}

module arm_right() {
    difference() {
        intersection() {
            union() {
                for (p = arm_seg) ell([p[0], p[1], p[2]], [p[3], p[4], p[5]]);
                for (i = [0:len(arm_seg) - 2]) hull() {
                    translate([arm_seg[i][0], arm_seg[i][1], arm_seg[i][2]]) sphere(r = 8.8);
                    translate([arm_seg[i+1][0], arm_seg[i+1][1], arm_seg[i+1][2]]) sphere(r = 8.8);
                }
                ell([42, -8.5, 80.5], [8.2, 8.5, 2.6]);                                          // cuff
                hull() { translate([43, -7.5, 86.5]) sphere(r = 7); ell([41, -9.5, 71.5], [8, 8.5, 8.5]); }  // wrist + fist
                hull() { translate([35.5, -13, 75]) sphere(r = 3.2); translate([35, -15, 70.5]) sphere(r = 3); }  // thumb
                shoulder_patch();
            }
            translate([shoulder_x, -60, 0]) cube([60, 120, 200]);
        }
        translate([shoulder_x - 1, 1, 135]) rotate([0, 90, 0]) cylinder(d = 2, h = 9, $fn = 16);
    }
}

module arm_left() { mirror([1, 0, 0]) arm_right(); }

// =====================================================================
//  BACKPACK (power box with two pockets)   (print upright)
// =====================================================================
bp_w = 48; bp_y0 = 14; bp_y1 = 40; bp_z0 = 92; bp_h = 50;

module backpack() {
    difference() {
        union() {
            translate([0, (bp_y0 + bp_y1)/2, bp_z0]) rounded_block(bp_w, bp_y1 - bp_y0, bp_h, 5);
            for (s = [-1, 1]) {
                translate([s * 11, bp_y1 + 2, 98]) rounded_block(17, 6, 26, 2);     // pocket
                translate([s * 11, bp_y1 + 2.6, 118]) rounded_block(18.4, 7.2, 8, 2);  // pocket flap
            }
        }
        // inner cavity, open towards the torso
        translate([0, (bp_y0 + bp_y1)/2 - 2.2, bp_z0 + 2.2]) rounded_block(bp_w - 4.4, bp_y1 - bp_y0, bp_h - 4.4, 3);
        // conform to the torso back and the neck ring
        minkowski() { torso_core(); sphere(r = 0.3, $fn = 8); }
        translate([0, 0, torso_top - 8]) cylinder(d = collar_od + 1, h = 40, $fn = 96);
        // USB-C breakout slot in the bottom
        translate([-5, 25, bp_z0 - 1]) cube([10, 5, 4]);
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
//  front: big visor with a thick bezel   back: "ear" pods + top band
// =====================================================================
pod_x = [36, 61]; pod_y = [0.05, 26]; pod_z = [5, 45];   // 0.05: keeps the preview clean, prints flush

module visor_shape(grow = 0) {
    translate([0, 0, visor_z]) rotate([90, 0, 0]) scale([1, visor_ratio, 1])
        cylinder(r = visor_r + grow, h = 60, $fn = 128);
}

module ear_pod() {   // right pod (+X)
    r = 4;
    difference() {
        intersection() {
            translate([pod_x[0], pod_y[0] - r, pod_z[0]]) rounded_cube([pod_x[1] - pod_x[0], pod_y[1] - pod_y[0] + r, pod_z[1] - pod_z[0]], r);
            translate([pod_x[0] - 1, pod_y[0], pod_z[0] - 1]) cube([pod_x[1] - pod_x[0] + 2, 40, pod_z[1] - pod_z[0] + 2]);   // flat seam face
        }
        // recessed face panel
        translate([pod_x[1] - 1.2, pod_y[0] + 4, pod_z[0] + 4]) rounded_cube([5, pod_y[1] - pod_y[0] - 8, pod_z[1] - pod_z[0] - 8], 1.5);
        // LED / sensor hole (optional helmet light)
        translate([30, 13, 14]) rotate([0, 90, 0]) cylinder(d = 5, h = 40, $fn = 24);
    }
    // studs
    for (y = [pod_y[0] + 7.5, pod_y[1] - 7.5], z = [pod_z[0] + 7.5, pod_z[1] - 7.5])
        translate([pod_x[1] - 1.2, y, z]) sphere(r = 1.7, $fn = 16);
}

module helmet_half(front = true) {
    difference() {
        union() {
            sphere(r = helmet_r, $fn = 128);
            if (front) intersection() {                                       // visor bezel
                sphere(r = helmet_r + 3.5, $fn = 128);
                difference() { visor_shape(5.5); visor_shape(0); }
            }
            if (!front) {
                ear_pod(); mirror([1, 0, 0]) ear_pod();
                intersection() {                                              // top band
                    difference() { sphere(r = helmet_r + 4, $fn = 128); sphere(r = helmet_r - 1, $fn = 128); }
                    translate([-60, 0.05, 22]) cube([120, 24, 40]);
                }
            }
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
        for (s = [-1, 1]) translate([s * 15, 0, 0]) for (z = knee_bands)
            translate([0, 0, z - 0.02]) scale([14.25, 14.75, 1]) cylinder(r = 1, h = 1.84, $fn = 48);
        translate([0.05, 0, 0]) shoulder_patch();
        mirror([1, 0, 0]) translate([0.05, 0, 0]) shoulder_patch();
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
    color("Gray") moon_base();
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
