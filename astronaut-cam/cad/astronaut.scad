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
torso_w    = 84;          // shoulder width (flat arm pads at +-42)
torso_z0   = 60;
torso_h    = 72;
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
// ellipsoid helper
module ell(c, r) { translate(c) scale(r) sphere(r = 1, $fn = 40); }

module leg(s) {
    K = [s * 17, -1, 36];      // knee
    A = [s * 17.5, 1, 15];     // ankle
    // thigh
    hull() {   // thigh with quadriceps and outer sweep
        ell([s * 15.5, 0, 58], [12.5, 13.5, 6]);
        ell([s * 16, -4, 49], [11, 10.5, 8]);
        ell([s * 18.5, -1, 46], [9.5, 9.5, 8]);
        translate(K) sphere(r = 8.5);
    }
    // knee cap
    translate(K + [0, -5.5, 0.5]) ell([0, 0, 0], [5, 4.5, 5]);
    // calf
    hull() { translate(K) sphere(r = 8.5); ell([s * 17, 3, 27], [9.5, 9.5, 7]); translate(A) sphere(r = 6.5); }
    // boot + cuff
    hull() {
        translate([s * 17.5, -10, 4]) scale([1, 1.3, 1]) cylinder(d = 20, h = 2);
        translate([s * 17.5, 1, 4]) cylinder(d = 22, h = 2);
        translate([s * 17.5, -1, 14]) cylinder(d = 17, h = 5);
    }
    translate([s * 17.5, -0.5, 17]) cylinder(d = 18.5, h = 3);
}

module legs() {
    // base plate
    hull() for (sx = [-1, 1], sy = [-1, 1]) translate([sx * 40, sy * 28, 0]) cylinder(r = 8, h = 4);
    intersection() {
        union() { leg(-1); leg(1); }
        translate([-60, -60, 0]) cube([120, 120, torso_z0]);
    }
    // pegs into the torso
    for (s = [-1, 1]) translate([s * 15.5, 0, torso_z0 - eps]) cylinder(d = 10, h = 4);
}

// =====================================================================
//  TORSO   (print upright; V-shaped athletic body)
// =====================================================================
// convex body core; o = inset (used to hollow it out)
module torso_core(o = 0) {
    hull() {   // pelvis -> waist
        translate([0, 0, torso_z0]) scale([27 - o, 20 - o, 1]) cylinder(r = 1, h = eps, $fn = 64);   // hips
        ell([0, 0, 67],  [27 - o, 20 - o, 7]);                           // pelvis
        ell([0, 1, 79],  [23.5 - o, 18.5 - o, 6]);                       // waist
    }
    hull() {   // waist -> chest -> shoulders (V-taper)
        ell([0, 1, 79],  [23.5 - o, 18.5 - o, 6]);                       // waist
        ell([0, -1, 107], [37 - o, 25 - o, 15]);                         // rib cage
        for (s = [-1, 1]) ell([s * 32, 2, 120], [13 - o, 13 - o, 13 - o]);   // shoulders
        ell([0, 3, 126], [22 - o, 17 - o, 7 - o]);                       // trapezius / neck base
    }
}

module torso_clip() { translate([-torso_w/2, -60, torso_z0]) cube([torso_w, 120, 100]); }

module torso_outer() { intersection() { torso_core(); torso_clip(); } }

module torso_muscles() {
    // pectorals: broad flat plates, underside blended into the chest for printing
    for (s = [-1, 1]) hull() {
        ell([s * 12.5, -21.5, 108], [13, 6.5, 7.5]);
        ell([s * 11, -19.5, 99], [10, 4, 2]);
        ell([s * 22, -16, 110], [6, 5, 6]);
    }
    // six-pack
    for (s = [-1, 1], i = [0:2]) ell([s * 5.6, -20.4 + i * 1.6, 93 - i * 6.5], [5, 2.6, 3.1]);
    // belt + buckle
    intersection() { torso_core(-1.5); translate([-50, -50, 64]) cube([100, 100, 7]); }
    translate([-7, -24, 64]) cube([14, 4, 7]);
}

module torso() {
    difference() {
        union() {
            torso_outer();
            intersection() { torso_muscles(); torso_clip(); }
            // neck collar
            translate([0, 0, torso_top - 8]) cylinder(d = collar_od, h = collar_top - torso_top + 8, $fn = 96);
        }
        // hollow inside (solid shoulder slab left under the deck)
        intersection() {
            torso_core(torso_wall);
            translate([-torso_w/2 + torso_wall, -60, torso_z0 + 3]) cube([torso_w - 2*torso_wall, 120, deck_bot - 4 - torso_z0 - 3]);
            // 45 deg conical ceiling -> prints without supports
            union() {
                translate([0, 0, deck_bot - 4 - 39]) cylinder(r1 = 60, r2 = 21, h = 39, $fn = 96);
                translate([0, 0, torso_z0]) cylinder(r = 60, h = deck_bot - 4 - 39 - torso_z0 + eps, $fn = 96);
            }
        }
        // deck seat and opening through the shoulder slab
        translate([0, 0, deck_bot]) cylinder(d = collar_id, h = 50, $fn = 96);
        translate([0, 0, deck_bot - 10]) cylinder(d = 44, h = 11, $fn = 96);
        // anti-rotation key slot for the deck
        translate([-collar_id/2 - 2, -2.2, deck_bot]) cube([4, 4.4, 50]);
        // window to the backpack
        translate([-18, 8, 74]) cube([36, 30, 50]);
        // leg pegs
        for (s = [-1, 1]) translate([s * 15.5, 0, torso_z0 - 1]) cylinder(d = 10.4, h = 6);
        // arm pins (1.75 mm filament) in the flat shoulder pads
        for (s = [-1, 1]) translate([s * torso_w/2, 2, 120]) rotate([0, 90, 0]) cylinder(d = 2, h = 12, center = true, $fn = 16);
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
    difference() {
        intersection() {
            union() {
                ell([45, 2, 120], [11.5, 12, 12]);                                        // deltoid
                hull() {                                                                  // upper arm: biceps + triceps
                    translate([47, 2, 115]) sphere(r = 8.5);
                    ell([50, -3, 104], [7.5, 8.5, 9]);
                    ell([51, 5.5, 106], [7, 6.5, 9]);
                    translate([51, 1, 92]) sphere(r = 6.5);
                }
                hull() {                                                                  // forearm
                    translate([51, 0, 91]) sphere(r = 6.8);
                    ell([51, -2.5, 84], [7.5, 8, 6]);
                    translate([50, -6, 71]) sphere(r = 5);
                }
                hull() for (dx = [-2.5, 2.5], dy = [-3.5, 2.5], dz = [-4, 3.5])           // gloved fist
                    translate([50 + dx, -8 + dy, 62 + dz]) sphere(r = 4.2, $fn = 24);
                translate([50, -6, 70]) cylinder(d = 13, h = 4, center = true);            // glove cuff
            }
            translate([torso_w/2, -50, 0]) cube([40, 100, 200]);
        }
        translate([torso_w/2 - 1, 2, 120]) rotate([0, 90, 0]) cylinder(d = 2, h = 9, $fn = 16);
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
