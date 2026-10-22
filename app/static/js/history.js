// Price history chart for the order pages (type.phtml), drawn with uPlot from
// /api/history/<location>/<typeid>/. Controls (all optional in the markup):
// range buttons, log scale, a volume-on-market panel and CSV download of the
// visible range.
(function() {
    var el = document.getElementById('pricehistory');
    if (!el || !window.uPlot) {
        return;
    }
    var volumeEl = document.getElementById('historyvolumechart');
    var select = document.getElementById('historylocation');
    var logBox = document.getElementById('historylog');
    var volumeBox = document.getElementById('historyvolume');
    var csvButton = document.getElementById('historycsv');
    var rangeButtons = Array.prototype.slice.call(document.querySelectorAll('.historyranges button'));
    var typeid = el.dataset.type;
    var location = el.dataset.location;
    var plot = null;
    var volumePlot = null;
    var lastData = null;
    var columns = null;
    var rangeDays = 90;
    // Snapshots more than this far apart are left unjoined rather than drawn
    // as a straight line across the gap (older months have one a day).
    var GAP = 30 * 3600;
    var SYNC = 'pricehistory';
    // Blue for buy, orange for sell: told apart by lightness as well as hue.
    var BUY = '#2f6fbf';
    var SELL = '#e08a1e';
    var full = new Intl.NumberFormat(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2});
    var whole = new Intl.NumberFormat(undefined, {maximumFractionDigits: 0});
    var compact = new Intl.NumberFormat(undefined, {notation: 'compact', maximumFractionDigits: 2});

    function num(value) {
        return value === null ? null : parseFloat(value);
    }

    function cssVar(name) {
        return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    }

    // Dates are year-month-day in UTC (EVE time), whatever the viewer's locale.
    function pad(n) {
        return n < 10 ? '0' + n : String(n);
    }

    function ymd(seconds) {
        var d = new Date(seconds * 1000);
        return d.getUTCFullYear() + '-' + pad(d.getUTCMonth() + 1) + '-' + pad(d.getUTCDate());
    }

    function hm(seconds) {
        var d = new Date(seconds * 1000);
        return pad(d.getUTCHours()) + ':' + pad(d.getUTCMinutes());
    }

    function price(u, value) {
        return value === null || value === undefined ? '-' : full.format(value);
    }

    function quantity(u, value) {
        return value === null || value === undefined ? '-' : whole.format(value);
    }

    function seconds(row) {
        return Date.parse(row.snapshot.replace(' ', 'T') + 'Z') / 1000;
    }

    function destroy() {
        if (plot) {
            plot.destroy();
            plot = null;
        }
        if (volumePlot) {
            volumePlot.destroy();
            volumePlot = null;
        }
    }

    function message(text) {
        destroy();
        el.textContent = text;
    }

    // [times, sell 5%, min sell, buy 5%, max buy, sell volume, buy volume], with
    // an all-null point in the middle of each gap so uPlot breaks the line there.
    function buildColumns(rows) {
        var cols = [[], [], [], [], [], [], []];
        var previous = null;
        rows.forEach(function(r) {
            var t = seconds(r);
            if (previous !== null && t - previous > GAP) {
                cols[0].push((previous + t) / 2);
                for (var i = 1; i < cols.length; i++) {
                    cols[i].push(null);
                }
            }
            cols[0].push(t);
            cols[1].push(num(r.sellpercentile));
            cols[2].push(num(r.sellmin));
            cols[3].push(num(r.buypercentile));
            cols[4].push(num(r.buymax));
            cols[5].push(num(r.sellvolume));
            cols[6].push(num(r.buyvolume));
            previous = t;
        });
        return cols;
    }

    function timeAxis(text, grid) {
        return {stroke: text, grid: {stroke: grid}, ticks: {stroke: grid}, space: 90,
                values: function(u, splits, axisIdx, space, incr) {
                    return splits.map(function(ts) { return incr < 86400 ? ymd(ts) + ' ' + hm(ts) : ymd(ts); });
                }};
    }

    function valueAxis(text, grid) {
        return {stroke: text, grid: {stroke: grid}, ticks: {stroke: grid}, size: 70,
                values: function(u, values) {
                    return values.map(function(v) { return v === null ? '' : compact.format(v); });
                }};
    }

    function timeSeries() {
        return {label: 'Time (UTC)', value: function(u, ts) { return ts === null ? '-' : ymd(ts) + ' ' + hm(ts); }};
    }

    // Place ticks on UTC day boundaries rather than the viewer's local midnight.
    function utcDate(ts) {
        return uPlot.tzDate(new Date(ts * 1000), 'Etc/UTC');
    }

    function currentWindow() {
        return plot ? {min: plot.scales.x.min, max: plot.scales.x.max} : null;
    }

    function markRange(days) {
        rangeDays = days;
        rangeButtons.forEach(function(button) {
            var active = days !== null && Number(button.dataset.days) === days;
            button.classList.toggle('active', active);
            button.setAttribute('aria-pressed', active ? 'true' : 'false');
        });
    }

    function applyRange() {
        if (!plot || rangeDays === null) {
            return;
        }
        var times = columns[0];
        var max = times[times.length - 1];
        var min = rangeDays ? Math.max(times[0], max - rangeDays * 86400) : times[0];
        plot.setScale('x', {min: min, max: max});
    }

    function drawVolume(timeWindow) {
        if (volumePlot) {
            volumePlot.destroy();
            volumePlot = null;
        }
        if (!volumeEl || !volumeBox || !volumeBox.checked || !columns) {
            if (volumeEl) {
                volumeEl.hidden = true;
            }
            return;
        }
        volumeEl.hidden = false;
        var text = cssVar('--color-text');
        var grid = cssVar('--color-table-stripe');
        var points = {fill: cssVar('--color-bg')};
        volumePlot = new uPlot({
            width: el.clientWidth,
            height: 140,
            tzDate: utcDate,
            cursor: {sync: {key: SYNC}, drag: {x: false, y: false}},
            series: [
                timeSeries(),
                {label: 'Sell volume', stroke: SELL, fill: SELL + '33', width: 1, points: points, value: quantity},
                {label: 'Buy volume', stroke: BUY, fill: BUY + '33', width: 1, points: points, value: quantity}
            ],
            axes: [timeAxis(text, grid), valueAxis(text, grid)]
        }, [columns[0], columns[5], columns[6]], volumeEl);
        if (timeWindow) {
            volumePlot.setScale('x', timeWindow);
        }
    }

    // keepWindow: redraw over the same time window (theme or log-scale change,
    // switching hub) rather than reapplying the range buttons.
    function draw(data, keepWindow) {
        var timeWindow = keepWindow ? currentWindow() : null;
        lastData = data;
        var rows = data.history;
        if (rows.length < 2) {
            columns = null;
            message('Not enough price history yet. It is recorded four times a day.');
            drawVolume(null);
            return;
        }
        columns = buildColumns(rows);
        var text = cssVar('--color-text');
        var grid = cssVar('--color-table-stripe');
        var points = {fill: cssVar('--color-bg')};
        var log = logBox && logBox.checked;
        var opts = {
            width: el.clientWidth,
            height: 300,
            tzDate: utcDate,
            cursor: {sync: {key: SYNC}},
            scales: {y: log ? {distr: 3, log: 10} : {}},
            series: [
                timeSeries(),
                {label: 'Sell (lowest 5%)', stroke: SELL, width: 2, points: points, value: price},
                {label: 'Min sell', stroke: SELL, width: 1, dash: [4, 4], points: points, value: price},
                {label: 'Buy (highest 5%)', stroke: BUY, width: 2, points: points, value: price},
                {label: 'Max buy', stroke: BUY, width: 1, dash: [4, 4], points: points, value: price}
            ],
            axes: [timeAxis(text, grid), valueAxis(text, grid)],
            hooks: {
                // Keep the volume panel on the same time window.
                setScale: [function(u, key) {
                    if (key === 'x' && volumePlot) {
                        volumePlot.setScale('x', {min: u.scales.x.min, max: u.scales.x.max});
                    }
                }],
                // A drag-zoom is a custom range: no button is active.
                setSelect: [function(u) {
                    if (u.select.width > 0) {
                        markRange(null);
                    }
                }]
            }
        };
        destroy();
        el.textContent = '';
        plot = new uPlot(opts, columns.slice(0, 5), el);
        // uPlot's double-click resets to all the data.
        plot.over.addEventListener('dblclick', function() { markRange(0); });
        drawVolume(timeWindow);
        if (timeWindow) {
            plot.setScale('x', timeWindow);
        } else {
            applyRange();
        }
    }

    function load(newLocation, keepWindow) {
        location = newLocation;
        fetch('/api/history/' + location + '/' + typeid + '/')
            .then(function(response) { return response.json(); })
            .then(function(data) { draw(data, keepWindow); })
            .catch(function() { message('Price history could not be loaded.'); });
    }

    function csvField(value) {
        return value === null || value === undefined ? '' : String(value);
    }

    // The raw API values (exact, as stored) for the snapshots in the visible range.
    function downloadCsv() {
        if (!lastData || !plot) {
            return;
        }
        var shown = currentWindow();
        var fields = ['snapshot', 'sellpercentile', 'sellmin', 'sellvolume', 'sellorders',
                      'buypercentile', 'buymax', 'buyvolume', 'buyorders'];
        var lines = ['snapshot_utc,sell_percentile,sell_min,sell_volume,sell_orders,' +
                     'buy_percentile,buy_max,buy_volume,buy_orders'];
        lastData.history.forEach(function(row) {
            var t = seconds(row);
            if (t >= shown.min && t <= shown.max) {
                lines.push(fields.map(function(f) { return csvField(row[f]); }).join(','));
            }
        });
        var link = document.createElement('a');
        link.href = URL.createObjectURL(new Blob([lines.join('\n') + '\n'], {type: 'text/csv'}));
        link.download = 'price-history-' + typeid + '-' + location + '.csv';
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
        setTimeout(function() { URL.revokeObjectURL(link.href); }, 1000);
    }

    rangeButtons.forEach(function(button) {
        button.addEventListener('click', function() {
            markRange(Number(button.dataset.days));
            applyRange();
        });
    });
    if (select) {
        select.addEventListener('change', function() { load(select.value, true); });
    }
    if (logBox) {
        logBox.addEventListener('change', function() {
            if (lastData) {
                draw(lastData, true);
            }
        });
    }
    if (volumeBox) {
        volumeBox.addEventListener('change', function() { drawVolume(currentWindow()); });
    }
    if (csvButton) {
        csvButton.addEventListener('click', downloadCsv);
    }
    window.addEventListener('resize', function() {
        if (plot) {
            plot.setSize({width: el.clientWidth, height: 300});
        }
        if (volumePlot) {
            volumePlot.setSize({width: el.clientWidth, height: 140});
        }
    });
    // Axis colours come from the theme, so redraw when it is toggled or the
    // system setting changes.
    function redraw() {
        if (lastData) {
            draw(lastData, true);
        }
    }
    var toggle = document.getElementById('theme-toggle');
    if (toggle) {
        toggle.addEventListener('click', redraw);
    }
    if (window.matchMedia) {
        window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', redraw);
    }
    load(location, false);
})();
