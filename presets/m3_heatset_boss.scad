// M3 heat-set insert boss (Ruthex / Voron style) — parametric, millimetres.
// Insert seat is dimensioned for a standard M3 x 5.7 mm brass insert.
// Good first issue: add a `shell_thickness` guard that warns below 1.2 mm.

$fn = 64;

boss_od      = 7.0;   // outer diameter of the boss
boss_height  = 8.0;   // total boss height (must exceed insert depth)
insert_od    = 4.6;   // heat-set insert barrel diameter + 0.2 mm
insert_depth = 5.7;   // insert length + 0.1 mm
pilot_d      = 3.4;   // M3 pilot / screw shaft clearance

rib_count    = 4;
rib_length   = 2.4;
rib_width    = 1.6;
rib_height   = 1.2;

module boss_body() {
    cylinder(d = boss_od, h = boss_height);
}

module insert_seat() {
    translate([0, 0, boss_height - insert_depth])
        cylinder(d = insert_od, h = insert_depth + 0.1);
}

module pilot_bore() {
    translate([0, 0, -0.1])
        cylinder(d = pilot_d, h = boss_height + 0.2);
}

module ribs() {
    for (a = [0 : 360 / rib_count : 359])
        rotate([0, 0, a])
            translate([boss_od / 2 - rib_length / 2, -rib_width / 2, 0])
                cube([rib_length, rib_width, boss_height - rib_height]);
}

module m3_heatset_boss() {
    difference() {
        union() {
            boss_body();
            ribs();
        }
        insert_seat();
        pilot_bore();
    }
}

m3_heatset_boss();
