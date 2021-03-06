# -*- coding: utf-8 -*-

import sys
from pathlib import Path
ROOT_PATH = Path().resolve().parent.parent
if str(ROOT_PATH) not in sys.path:
    sys.path.insert(str(ROOT_PATH))
import numpy as np
import scipy
from frequency_response import FrequencyResponse


def limited_slope_plots(fr, limit):
    fr.equalization = -fr.error
    limited, smoothed, limited_forward, clipped_forward, limited_backward, clipped_backward, peak_inds, dip_inds, \
        backward_start, protection_mask = limited_slope(fr.frequency, fr.equalization, limit)

    x = fr.frequency.copy()
    y = smoothed

    # Plot graphs
    fig, ax = fr.plot_graph(
        show=False, raw=False, error=False, target=False, equalization_plot_kwargs={
            'color': 'C2', 'linewidth': 1, 'label': 'Raw equalization', 'linestyle': 'dashed'
        })
    fig.set_size_inches(20, 9)
    ax.plot(x, y, label='Smoothed equalization', color='C2')
    ax.plot(x, limited, label='Limited', color='C1')
    ax.fill_between(x, clipped_forward * -5, clipped_forward * 10, label='Limited left to right', color='blue',
                    alpha=0.1)
    ax.fill_between(x, clipped_backward * -10, clipped_backward * 5, label='Limited right to left', color='red',
                    alpha=0.1)
    ax.fill_between(x, protection_mask * -12, protection_mask * 12, label='Limitation-safe zone', color='limegreen',
                    alpha=0.2)
    ax.scatter(x[peak_inds], y[peak_inds], color='red')
    ax.scatter(x[backward_start], y[backward_start], 200, marker='<', label='Backward start', color='black')
    ax.scatter(x[dip_inds], y[dip_inds], color='limegreen')
    ax.legend()

    return fig, ax


def limited_slope(x, y, limit):
    """Bi-directional slope limitation for a frequency response curve

    Args:
            x: frequencies
            y: amplitudes
        limit:

    Returns:

    """
    fr = FrequencyResponse(name='fr', frequency=x, raw=y)
    # Smoothen data, heavily on treble to avoid problems in +10 kHz region
    fr.smoothen_fractional_octave(window_size=1 / 12, treble_window_size=2, treble_f_lower=9000, treble_f_upper=11500)

    # Copy data
    x = fr.frequency.copy()
    y = fr.smoothed.copy()

    # Find peaks and notches
    # TODO: these affect which regions are rejected
    peak_inds, peak_props = scipy.signal.find_peaks(y, prominence=1)
    dip_inds, dip_props = scipy.signal.find_peaks(-y, prominence=1)

    limit_free_mask = protection_mask(y, dip_inds)

    # Find backward start index
    backward_start = find_backward_start(y, peak_inds, dip_inds)  # TODO: backward start

    # Find forward and backward limitations
    # limited_forward is y but with slopes limited when traversing left to right
    # clipped_forward is boolean mask for limited samples when traversing left to right
    # limited_backward is found using forward algorithm but with flipped data
    limited_forward, clipped_forward, regions_forward = limited_forward_slope(
        x, y, limit, start_index=0, peak_inds=peak_inds, limit_free_mask=limit_free_mask)
    limited_backward, clipped_backward, regions_backward = limited_backward_slope(
        x, y, limit, start_index=backward_start, peak_inds=peak_inds, limit_free_mask=limit_free_mask)

    # TODO: Find notches which are lower in level than adjacent notches
    # TODO: Set detected notches as slope clipping free zones up to levels of adjacent notches

    # Forward and backward limited curves are combined with min function
    # Combination function is smoothed to get rid of hard kinks
    limiter = FrequencyResponse(
        name='limiter', frequency=x.copy(), raw=np.min(np.vstack([limited_forward, limited_backward]), axis=0))
    limiter.smoothen_fractional_octave(window_size=1 / 5, treble_window_size=1 / 5)
    #limiter.smoothed = limiter.raw.copy()

    return limiter.smoothed.copy(), fr.smoothed.copy(), limited_forward, clipped_forward, limited_backward, clipped_backward, \
        peak_inds, dip_inds, backward_start, limit_free_mask


def protection_mask(y, dip_inds):
    """Finds zones around dips which are lower than their adjacent dips. Zones extend to the lower level of the adjacent
    dips.

    Args:
        x: frequencies
        y: amplitudes
        dip_inds: Indices of dips

    Returns:
        Boolean mask for limitation-free indices
    """
    mask = np.zeros(len(y)).astype(bool)
    if len(dip_inds) < 3:
        return mask
    # Find peaks which are lower in level than their adjacent dips
    dip_levels = y[dip_inds]
    # First row contains levels of previous dips
    # Second row contains levels of current dips
    # Third row contains levels of next dips
    # First and last dips are ignored because they don't have both adjacent dips
    stack = np.vstack([dip_levels[2:], dip_levels[1:-1], dip_levels[:-2]])
    # Boolean mask for dips which are lower than their adjacent dips
    null_mask = np.concatenate([[False], np.argmin(stack, axis=0) == 1, [False]])
    # Indices of dips which are lower than their adjacent dips
    null_inds = np.argwhere(null_mask)[:, 0]
    if len(null_inds) < 1:
        return mask
    # First column is the level of the previous dip
    # Second column is the level of the next dip
    adjacent_dip_levels = np.vstack([dip_levels[null_inds - 1], dip_levels[null_inds + 1]])
    adjacent_dip_levels = np.transpose(adjacent_dip_levels)
    # Find indexes on both sides where the curve goes above the adjacent dips minimum level
    for i in range(len(null_inds)):
        dip_ind = dip_inds[null_inds[i]]
        target_left = adjacent_dip_levels[i, 0]
        target_right = adjacent_dip_levels[i, 1]
        # TODO: Should left and right side targets be separate?
        #target = np.min([target_left, target_right])
        # TODO: Should target be where gradient reduces below certain threshold?
        left_ind = np.argwhere(y[:dip_ind] >= target_left)[-1, 0] + 1
        right_ind = np.argwhere(y[dip_ind:] >= target_right)
        right_ind = right_ind[0, 0] + dip_ind - 1
        mask[left_ind:right_ind + 1] = np.ones(right_ind - left_ind + 1).astype(bool)
    return mask


def limited_backward_slope(x, y, limit, start_index=0, peak_inds=None, limit_free_mask=None):
    """Limits forwards slope of a frequency response curve while traversing backwards

        Args:
            x: frequencies
            y: amplitudes
            limit: maximum slope in dB / oct
            start_index: Index where to start traversing, no limitations apply before this
            peak_inds: Peak indexes. Regions will require to touch one of these if given.
            limit_free_mask: Boolean mask for indices where limitation must not be applied

        Returns:
            limited: Limited curve
            mask: Boolean mask for clipped indexes
            regions: Clipped regions, one per row, 1st column is the start index, 2nd column is the end index (exclusive)
    """
    start_index = len(x) - start_index - 1
    if peak_inds is not None:
        peak_inds = len(x) - peak_inds - 1
    if limit_free_mask is not None:
        limit_free_mask = np.flip(limit_free_mask)
    limited_backward, clipped_backward, regions_backward = limited_forward_slope(
        x, np.flip(y), limit, start_index=start_index, peak_inds=peak_inds, limit_free_mask=limit_free_mask)
    limited_backward = np.flip(limited_backward)
    clipped_backward = np.flip(clipped_backward)
    regions_backward = len(x) - regions_backward - 1
    return limited_backward, clipped_backward, regions_backward


def limited_forward_slope(x, y, limit, start_index=0, peak_inds=None, limit_free_mask=None):
    """Limits forwards slope of a frequency response curve

    Args:
        x: frequencies
        y: amplitudes
        limit: maximum slope in dB / oct
        start_index: Index where to start traversing, no limitations apply before this
        peak_inds: Peak indexes. Regions will require to touch one of these if given.
        limit_free_mask: Boolean mask for indices where limitation must not be applied

    Returns:
        limited: Limited curve
        mask: Boolean mask for clipped indexes
        regions: Clipped regions, one per row, 1st column is the start index, 2nd column is the end index (exclusive)
    """
    if peak_inds is not None:
        peak_inds = np.array(peak_inds)

    limited = []
    clipped = []
    regions = []
    for i in range(len(x)):
        if i <= start_index:
            # No clipping before start index
            limited.append(y[i])
            clipped.append(False)
            continue

        # Calculate slope and local limit
        slope = log_log_gradient(x[i], x[i - 1], y[i], limited[-1])
        # Local limit is 25% of the limit between 8 kHz and 10 kHz
        # TODO: limit 9 kHz notch 8 kHz to 11 kHz?
        local_limit = limit / 4 if 8000 <= x[i] <= 11500 else limit

        if slope > local_limit and (limit_free_mask is None or not limit_free_mask[i]):
            # Slope between the two samples is greater than the local maximum slope, clip to the max
            if not clipped[-1]:
                # Start of clipped region
                regions.append([i])
            clipped.append(True)
            # Add value with limited change
            octaves = np.log(x[i] / x[i - 1]) / np.log(2)
            limited.append(limited[-1] + local_limit * octaves)

        else:
            # Moderate slope, no need to limit
            limited.append(y[i])

            if clipped[-1]:
                # Previous sample clipped but this one didn't, means it's the end of clipped region
                # Add end index to the region
                regions[-1].append(i + 1)

                region_start = regions[-1][0]
                if peak_inds is not None and not np.any(np.logical_and(peak_inds >= region_start, peak_inds < i)):
                    # None of the peak indices found in the current region, discard limitations
                    limited[region_start:i] = y[region_start:i]
                    clipped[region_start:i] = [False] * (i - region_start)
                    regions.pop()
            clipped.append(False)

    if len(regions) and len(regions[-1]) == 1:
        regions[-1].append(len(x) - 1)

    return np.array(limited), np.array(clipped), np.array(regions)


def log_log_gradient(f0, f1, g0, g1):
    octaves = np.log(f1 / f0) / np.log(2)
    gain = g1 - g0
    return gain / octaves


def find_backward_start(y, peak_inds, notch_inds):
    # Find starting index for the backward pass
    if peak_inds[-1] > notch_inds[-1]:
        # Last peak is a positive peak
        # Find index on the right side of the peak where the curve crosses the left side minimum
        backward_start = np.argwhere(y[peak_inds[-1]:] <= y[notch_inds[-1]])
        if len(backward_start):
            backward_start = backward_start[0, 0] + peak_inds[-1]
        else:
            backward_start = len(y) - 1
    else:
        # Last peak is a negative peak, start there
        backward_start = notch_inds[-1]
    return backward_start
