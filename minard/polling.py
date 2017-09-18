from .db import engine 

# PMT Type defines
LOWG     = 0x21
NONE     = 0x0
NECK     = 0x9
FECD     = 0x10
BUTT     = 0x81

OWLS = ((18,15),(13,15),(3,15))

def polling_runs():
    """
    Returns two lists of runs, one where CMOS rates were polled using check
    rates, the other where base currents were polled using check rates.
    """
    conn = engine.connect()

    result = conn.execute("SELECT DISTINCT ON (run) run FROM cmos ORDER BY run DESC")

    if result is not None:
        keys = result.keys()
        rows = result.fetchall()
        cmos_runs = [dict(zip(keys,row)) for row in rows]

    result = conn.execute("SELECT DISTINCT ON (run) run FROM base ORDER BY run DESC")

    if result is not None:
        keys = result.keys()
        rows = result.fetchall()
        base_runs = [dict(zip(keys,row)) for row in rows]

    return cmos_runs, base_runs

def polling_history(crate, slot, channel, min_run):
    """
    Return a list of form [[run number, cmos rate]] for all runs with cmos data
    polling. Also returns a list which included statistics on the cmos data.
    """
    conn = engine.connect()

    result = conn.execute("SELECT run, cmos_rate FROM cmos WHERE crate = %s "
                          "AND slot = %s AND channel = %s AND run > %s ORDER BY run DESC",
                           (crate, slot, channel, min_run))

    if result is None:
        return None, None

    keys = result.keys()
    rows = result.fetchall()

    data = []
    for run, rate in rows:
        data.append([int(run),rate])

    data_stats = []
    if data:
        z = zip(*data)
        data_max = max(z[1])
        data_min = min(z[1])
        data_average = sum(z[1])/(len(z[1]))

        data_std = 0
        for i in range(len(z[1])):
            data_std += (z[1][i] - data_average)**2
        data_std = (data_std/len(z[1]))**(0.5)

        data_stats = [int(data_max), int(data_min), int(data_average), int(data_std)]

    return data, data_stats


def polling_info(data_type, run_number):
    """
    Returns the polling data for the detector
    """
    conn = engine.connect()

    # Hold the polling information
    # for the entire detector
    data = [0]*9728

    # Default load the most recent run
    if run_number == 0:
        if data_type == "cmos":
            result = conn.execute("SELECT run FROM cmos ORDER by "
                                  "run DESC limit 1")
        elif data_type == "base":
            result = conn.execute("SELECT run FROM base ORDER by "
                                  "run DESC limit 1")
        else:
            return None

        if result is None:
            return None

        cmos_run = result.fetchone()

        for run in cmos_run:
            run_number = run

    if data_type == "cmos":
        result = conn.execute("SELECT DISTINCT ON (run, crate, slot, channel) "
                              "crate, slot, channel, cmos_rate FROM cmos WHERE run = %s "
                              "ORDER BY run, crate, slot, channel",
                              (run_number,))
    elif data_type == "base":
        result = conn.execute("SELECT DISTINCT ON (run, crate, slot, channel) "
                              "crate, slot, channel, base_current FROM base WHERE run = %s "
                              "ORDER BY run, crate, slot, channel",
                              (run_number,))
    else:
        return None

    if result is None:
        return None

    row = result.fetchall()
    for crate, card, channel, cmos_rate in row:
        lcn = crate*512 + card*32 + channel
        data[lcn] = cmos_rate

    return data


def most_recent_run(all_runs, requested_run):
    '''
    Loops over all CMOS/Base polling runs and
    returns the run closest (>) to the request run
    '''

    run = 0
    for runs in all_runs:
       # Get most recent run 
       if requested_run == 0:
          run = runs['run']
          break
       # Get most recent run after requested run
       if runs['run'] > requested_run:
          run = runs['run']

    return run


def polling_summary(run):
    ''' 
    Get the crate average base currents and cmos rates 
    for the run nearest to the requested run.
    '''

    # List of polling runs
    cmos_runs, base_runs = polling_runs()

    # Runs closest to requested run
    crun = most_recent_run(cmos_runs, run)
    brun = most_recent_run(base_runs, run)

    conn = engine.connect()

    # Used to discount channels known to be off
    relays = relay_status(conn)
    types = pmt_type(conn)
    channel_info = channel_information(conn)

    crate_average_cmos = [0.0]*20
    crate_average_base = [0.0]*20
    crates_cmos = [16*32]*20
    crates_base = [16*32]*20
    crates_cmos[19] = 3*32
    crates_base[19] = 3*32

    result = conn.execute("SELECT cmos_rate, crate, slot, channel FROM cmos "
                          "WHERE run = %s", (crun,))
    if result is None:
        return None

    row = result.fetchall()
    # Reject hits where the CMOS rates are too high and bias the average
    for cmos_rate, crate, slot, channel in row:
        lcn = crate*512 + slot*32 + channel
        if not check_hv_status(relays, types, channel_info, crate, slot, channel):
            if (crate, slot) in OWLS:
                crates_cmos[19]-=1
            else:
                crates_cmos[crate]-=1
            continue
        if cmos_rate < 1000000:
            if (crate, slot) in OWLS:
                crate_average_cmos[19] += float(cmos_rate)
                crates_cmos[crate]-=1
            else:
                crate_average_cmos[crate] += float(cmos_rate)
        else:
            crates_cmos[crate]-=1

    result = conn.execute("SELECT base_current, crate, slot, channel FROM base "
                          "WHERE run = %s", (brun,))

    if result is None:
        return None

    row = result.fetchall()
    for base_current, crate, slot, channel in row:
        lcn = crate*512 + slot*32 + channel
        if not check_hv_status(relays, types, channel_info, crate, slot, channel):
            if (crate, slot) in OWLS:
                crates_base[19]-=1
            else:
                if crate == 18:
                    print crate, slot, channel
                crates_base[crate]-=1
            continue
        # Ignore bad readback
        if base_current > -10:
            if (crate, slot) in OWLS:
                crate_average_base[19] += float(base_current)
                crates_base[crate]-=1
            else:
                crate_average_base[crate] += float(base_current)
        else:
            crates_base[crate]-=1

    crate_average = []
    for crate in range(20):
        cmos = round(float(crate_average_cmos[crate])/crates_cmos[crate], 2)
        base = round(float(crate_average_base[crate])/crates_base[crate], 2)
        crate_average.append((crate, cmos, base))

    return crate_average, crun, brun


def polling_check(high_rate, low_rate, pct_change):

    conn = engine.connect()

    run_number = []

    # Get the two most recent runs with valid cmos data
    result = conn.execute("SELECT DISTINCT ON (run) run FROM cmos ORDER BY "
                          "run DESC LIMIT 2")

    rows = result.fetchall()
    for run in rows:
        run_number.append(run[0])

    data_run1 = [0]*9728
    data_run2 = [0]*9728

    # Get the cmos data from the two most recent runs with valid data
    result = conn.execute("SELECT crate, slot, channel, cmos_rate, run FROM cmos WHERE "
                          "run = %s or run = %s", (run_number[0], run_number[1]))

    rows = result.fetchall()
    for crate, slot, channel, cmos_rate, run in rows:
        lcn = crate*512 + slot*32 + channel
        if run == run_number[0]:
            data_run1[lcn] = cmos_rate
        elif run == run_number[1]:
            data_run2[lcn] = cmos_rate

    zero_threshold = [0]*9728
    threshold = [0]*9728

    # Get the discriminator thresholds
    result = conn.execute("SELECT DISTINCT ON (crate, slot) crate, slot, zero_disc "
                          "FROM zdisc ORDER BY crate, slot, timestamp DESC")

    rows = result.fetchall()

    for crate, slot, zero in rows:
        for i in range(len(zero)):
            lcn = crate*512 + slot*32 + i
            zero_threshold[lcn] = zero[i]

    # Get the discriminator zeros
    result = conn.execute("SELECT crate, slot, vthr FROM current_detector_state "
                          "ORDER BY crate, slot")

    rows = result.fetchall()

    for crate, slot, thresh in rows:
        for i in range(len(thresh)):
            lcn = crate*512 + slot*32 + i
            threshold[lcn] = thresh[i]

    # Get the information needed to determine whether a channel is online
    relays = relay_status(conn)
    types = pmt_type(conn)
    channel_info = channel_information(conn)

    cmos_changes = []
    cmos_high_rates = []
    cmos_low_rates = []

    # Loop through the channels and warn about misbehaving channels
    for crate in range(19):
        for slot in range(16):
            for channel in range(32):
                lcn = crate*512 + slot*32 + channel
                # Warn about any high rate channel
                if data_run1[lcn] > high_rate:
                    vthr = threshold[lcn] - zero_threshold[lcn]
                    cmos_high_rates.append("%i/%i/%i: %i Hz, Vthr: %i" %\
                            (crate, slot, channel, data_run1[lcn], vthr))
                elif data_run2[lcn] > high_rate:
                    vthr = threshold[lcn] - zero_threshold[lcn]
                    cmos_high_rates.append("%i/%i/%i: %i Hz, Vthr: %i" %\
                            (crate, slot, channel, data_run2[lcn], vthr))
                if not check_hv_status(relays, types, channel_info, crate, slot, channel):
                    continue
                # For normal/HQE online PMTs, warn about channels
                # that are fluctuating a lot. Put a low rate cut
                # so that we don't warn about channels we don't care about 
                if data_run1[lcn] > 50 and data_run2[lcn] > 50:
                    change1 = 100*((data_run2[lcn] - data_run1[lcn])/data_run1[lcn])
                    change2 = 100*((data_run1[lcn] - data_run2[lcn])/data_run2[lcn])
                    if change1 > pct_change or change2 > pct_change:
                        cmos_changes.append("%i/%i/%i: %i Hz to %i Hz" %\
                            (crate, slot, channel, data_run1[lcn], data_run2[lcn]))
                # If the channel is not marked as low/zero/disc warn about low rates
                if not channel_info[lcn][1]:
                    if data_run1[lcn] < low_rate:
                        cmos_low_rates.append("%i/%i/%i: %i Hz" %\
                            (crate, slot, channel, data_run1[lcn]))
                    elif data_run2[lcn] < low_rate:
                        cmos_low_rates.append("%i/%i/%i: %i Hz" %\
                            (crate, slot, channel, data_run2[lcn]))

    return cmos_changes, cmos_high_rates, cmos_low_rates, run_number


def check_hv_status(relays, types, channel_info, crate, slot, channel):

    lcn = crate*512 + slot*32 + channel

    # Check if the channel is at high voltage
    hv_relay_mask = relays[crate][1] << 32 | relays[crate][0]
    if not(hv_relay_mask & (1 << (slot*4 + (3-channel//8)))):
        return 0
    # Check the PMT is normal or HQE
    if types[lcn] in (LOWG, NECK, FECD, BUTT, NONE):
        return 0
    # Check the resistor is not pulled
    if channel_info[lcn][0]:
        return 0

    return 1


def pmt_type(conn):
    """
    Get the PMT types
    """
    types = [0]*9728
    sql_result = conn.execute("SELECT crate, slot, channel, type FROM pmt_info "
                              "ORDER BY crate, slot, channel")

    sql_result = sql_result.fetchall()
    for crate, slot, channel, pmttype in sql_result:
        lcn = crate*512 + slot*32 + channel
        types[lcn] = pmttype

    return types

def channel_information(conn):
    """
    Get the channel status (whether its rpulled or occupancy issues)
    """
    channel_info = [0]*9728
    sql_result = conn.execute("SELECT crate, slot, channel, resistor_pulled, zero_occupancy, "
                              "low_occupancy, bad_discriminator FROM current_channel_status ORDER "
                              "BY crate, slot, channel")

    sql_result = sql_result.fetchall()
    for crate, slot, channel, rpulled, low_occ, zero_occ, bad_disc in sql_result:
        lcn = crate*512 + slot*32 + channel
        channel_info[lcn] = [rpulled, (low_occ | zero_occ | bad_disc)]

    return channel_info

def relay_status(conn):
    relays = []
    result = conn.execute("SELECT hv_relay_mask1, hv_relay_mask2 FROM "
                          "current_crate_state ORDER BY crate")

    rows = result.fetchall()

    for hv_relay_mask1, hv_relay_mask2 in rows:
        relays.append([hv_relay_mask1, hv_relay_mask2])

    return relays

def polling_info_card(data_type, run_number, crate):
    """
    Returns the polling data for a crate
    """
    conn = engine.connect()

    # Hold the polling information
    # for a single crate
    data = [0]*512

    # Default load the most recent run
    if run_number == 0:
        if data_type == "cmos":
            result = conn.execute("SELECT run FROM cmos ORDER by "
                                  "run DESC limit 1")
        elif data_type == "base":
            result = conn.execute("SELECT run FROM base ORDER by "
                                  "run DESC limit 1")
        else:
            return None

        if result is None:
            return None

        cmos_run = result.fetchone()

        for run in cmos_run:
            run_number = run

    if data_type == "cmos":
        result = conn.execute("SELECT DISTINCT ON (run, crate, slot, channel) "
                              "slot, channel, cmos_rate FROM cmos WHERE run = %s "
                              "AND crate = %s ORDER by run, slot, channel",
                              (run_number, crate))
    elif data_type == "base":
        result = conn.execute("SELECT DISTINCT ON (run, crate, slot, channel) "
                              "slot, channel, base_current FROM base WHERE run = %s "
                              "AND crate = %s ORDER by run, slot, channel",
                              (run_number, crate))
    else:
        return None

    if result is None:
        return None

    row = result.fetchall()
    for card, channel, cmos_rate in row:
        data[card*32+channel] = cmos_rate

    return data
