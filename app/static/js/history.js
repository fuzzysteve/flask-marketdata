// Price history chart for the order pages (type.phtml), drawn with uPlot from
// /api/history/<location>/<typeid>/.
(function() {
    var el = document.getElementById('pricehistory');
    if (!el || !window.uPlot) {
        return;
    }
    var select = document.getElementById('historylocation');
    var typeid = el.dataset.type;
    var plot = null;
    var lastData = null;
    // Blue for buy, orange for sell: told apart by lightness as well as hue.
    var BUY = '#2f6fbf';
    var SELL = '#e08a1e';
    var full = new Intl.NumberFormat(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2});
    var compact = new Intl.NumberFormat(undefined, {notation: 'compact', maximumFractionDigits: 2});

    function num(value) {
        return value === null ? null : parseFloat(value);
    }

    function cssVar(name) {
        return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    }

    function price(u, value) {
        return value === null || value === undefined ? '-' : full.format(value);
    }

    function message(text) {
        if (plot) {
            plot.destroy();
            plot = null;
        }
        el.textContent = text;
    }

    function draw(data) {
        lastData = data;
        var rows = data.history;
        if (rows.length < 2) {
            message('Not enough price history yet. It is recorded four times a day.');
            return;
        }
        var text = cssVar('--color-text');
        var grid = cssVar('--color-table-stripe');
        var points = {fill: cssVar('--color-bg')};
        var columns = [
            rows.map(function(r) { return Date.parse(r.snapshot.replace(' ', 'T') + 'Z') / 1000; }),
            rows.map(function(r) { return num(r.sellpercentile); }),
            rows.map(function(r) { return num(r.sellmin); }),
            rows.map(function(r) { return num(r.buypercentile); }),
            rows.map(function(r) { return num(r.buymax); })
        ];
        var axis = {stroke: text, grid: {stroke: grid}, ticks: {stroke: grid}};
        var opts = {
            width: el.clientWidth,
            height: 300,
            series: [
                {},
                {label: 'Sell (lowest 5%)', stroke: SELL, width: 2, points: points, value: price},
                {label: 'Min sell', stroke: SELL, width: 1, dash: [4, 4], points: points, value: price},
                {label: 'Buy (highest 5%)', stroke: BUY, width: 2, points: points, value: price},
                {label: 'Max buy', stroke: BUY, width: 1, dash: [4, 4], points: points, value: price}
            ],
            axes: [
                axis,
                Object.assign({}, axis, {size: 70, values: function(u, values) {
                    return values.map(function(v) { return compact.format(v); });
                }})
            ]
        };
        if (plot) {
            plot.destroy();
        }
        el.textContent = '';
        plot = new uPlot(opts, columns, el);
    }

    function load(location) {
        fetch('/api/history/' + location + '/' + typeid + '/')
            .then(function(response) { return response.json(); })
            .then(draw)
            .catch(function() { message('Price history could not be loaded.'); });
    }

    if (select) {
        select.addEventListener('change', function() { load(select.value); });
    }
    window.addEventListener('resize', function() {
        if (plot) {
            plot.setSize({width: el.clientWidth, height: 300});
        }
    });
    // Axis colours come from the theme, so redraw when it is toggled or the
    // system setting changes.
    function redraw() {
        if (lastData) {
            draw(lastData);
        }
    }
    var toggle = document.getElementById('theme-toggle');
    if (toggle) {
        toggle.addEventListener('click', redraw);
    }
    if (window.matchMedia) {
        window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', redraw);
    }
    load(el.dataset.location);
})();
