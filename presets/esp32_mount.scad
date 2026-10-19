// ESP32-S3 DevKitC-1 snap-fit mount — parametric, millimetres.
// Print: 0.4 mm nozzle, 0.2 mm layers, 3 perimeters, no supports required.
// Good first issue: add a USB-C cutout variant + antenna keep-out.

$fn = 48;

board_length = 63.0;
board_width  = 25.5;
board_thick  = 1.6;

wall         = 2.0;
floor        = 2.4;
wall_height  = 6.0;
clearance    = 0.35;

usb_width    = 9.4;
usb_height   = 3.6;

outer_length = board_length + 2 * wall + clearance;
outer_width  = board_width  + 2 * wall + clearance;

module floor_plate() {
    cube([outer_length, outer_width, floor]);
}

module perimeter_wall() {
    difference() {
        cube([outer_length, outer_width, floor + wall_height]);
        translate([wall, wall, floor])
            cube([outer_length - 2 * wall, outer_width - 2 * wall, wall_height + 1]);
    }
}

module snap_lips() {
    // two cantilever lips that retain the PCB on the long edges
    for (y = [wall + 1.5, outer_width - wall - 3.5]) {
        translate([outer_length / 2 - 9, y, floor + wall_height - 1.2])
            cube([18, 2.0, 1.2]);
    }
}

module usb_cutout() {
    translate([-0.1, (outer_width - usb_width) / 2, floor + 0.6])
        cube([wall + 0.2, usb_width, usb_height]);
}

module vent_slots(count = 5, slot = 2.0) {
    for (i = [0 : count - 1])
        translate([12 + i * (slot + 3), outer_width / 2 - 8, -0.1])
            cube([slot, 16, floor + 0.2]);
}

module esp32_mount() {
    difference() {
        union() {
            floor_plate();
            perimeter_wall();
            snap_lips();
        }
        usb_cutout();
        vent_slots();
    }
}

esp32_mount();
