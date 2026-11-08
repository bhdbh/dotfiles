#ifndef WAYBAR_VOLUME_CURVE_H
#define WAYBAR_VOLUME_CURVE_H

#include <math.h>

/* Spread a reference subrange over the remainder of the displayed scale. */
static inline int volume_curve_has_stretch(double start, double end) {
    return start >= 0 && start < end && end <= 100;
}

static inline double volume_curve_stretch_server(double percent, double start, double end) {
    if (percent > 100) percent = 100;
    if (!volume_curve_has_stretch(start, end) || percent <= start) return percent;
    return start + (percent - start) * (end - start) / (100 - start);
}

static inline double volume_curve_stretch_display(double percent, double start, double end) {
    if (!volume_curve_has_stretch(start, end) || percent <= start) return percent;
    return start + (percent - start) * (100 - start) / (end - start);
}

/* An optional pivot shapes quiet levels without changing the upper curve.
 * An explicit pivot slope enables a cubic lower section; start-slope controls
 * its first steps without changing the slope or value at the upper join.
 * Zero still sends actual zero to the server, ensuring complete silence. */
static inline int volume_curve_has_pivot(double floor, double ceiling, double pivot, double level, double slope, double start) {
    if (!(pivot > 0 && pivot < 100 && level > floor && level < ceiling)) return 0;
    if (!(start >= 0) || !isfinite(start)) return 0;
    if (slope > 0) {
        /* The derivative of the lower cubic must stay nonnegative. */
        double range = level - floor;
        double a = 3 * (pivot * (start + slope) - 2 * range);
        double b = 2 * (3 * range - pivot * (2 * start + slope));
        double c = pivot * start;
        if (a > 0 && b < 0 && -b < 2 * a && c - b * b / (4 * a) < 0) return 0;
    }
    if (!(slope > 0)) slope = (level - floor) / pivot;
    return slope <= 3 * (level - floor) / pivot &&
        slope <= (ceiling - level) / (100 - pivot);
}

static inline double volume_curve_lower(double percent, double floor, double pivot, double level, double slope, double start) {
    if (!(slope > 0)) return floor + percent * (level - floor) / pivot;
    double t = percent / pivot;
    return floor + t * t * ((3 - 2 * t) * (level - floor) + (t - 1) * pivot * slope)
        + pivot * start * t * (1 - t) * (1 - t);
}

static inline double volume_curve_display(double raw, double floor, double ceiling, double pivot, double level, double slope, double start) {
    if (raw <= floor) return 0;
    if (!volume_curve_has_pivot(floor, ceiling, pivot, level, slope, start))
        return (raw - floor) * 100.0 / (ceiling - floor);
    if (raw <= level) {
        if (!(slope > 0)) return (raw - floor) * pivot / (level - floor);
        double low = 0, high = pivot;
        for (int i = 0; i < 48; i++) {
            double mid = (low + high) / 2;
            if (volume_curve_lower(mid, floor, pivot, level, slope, start) < raw) low = mid;
            else high = mid;
        }
        return (low + high) / 2;
    }
    if (!(slope > 0)) slope = (level - floor) / pivot;
    double span = 100 - pivot;
    double bend = (ceiling - level - slope * span) / (span * span);
    double delta = raw - level;
    return pivot + 2 * delta / (slope + sqrt(slope * slope + 4 * bend * delta));
}

static inline double volume_curve_server(double percent, double floor, double ceiling, double pivot, double level, double slope, double start) {
    if (percent <= 0) return 0;
    if (percent > 100) percent = 100;
    if (!volume_curve_has_pivot(floor, ceiling, pivot, level, slope, start))
        return floor + percent * (ceiling - floor) / 100.0;
    if (percent <= pivot) return volume_curve_lower(percent, floor, pivot, level, slope, start);
    if (!(slope > 0)) slope = (level - floor) / pivot;
    double span = 100 - pivot;
    double bend = (ceiling - level - slope * span) / (span * span);
    double delta = percent - pivot;
    return level + slope * delta + bend * delta * delta;
}

#endif
