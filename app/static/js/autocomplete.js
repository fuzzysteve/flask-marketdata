// Type-ahead for the search inputs, replacing jQuery UI's autocomplete.
// autocomplete(input, url, select): url returns [{value, label}, ...] (the
// /api/*ids lists), fetched once. Like jQuery UI it starts at 2 characters and
// matches a case-insensitive substring of the label or the value.
// select(item) is called with the chosen entry.
function autocomplete(input, url, select) {
    var MIN_LENGTH = 2;
    // jQuery UI listed every match; ~20k types makes that slow to draw, and
    // the menu only shows a few rows at a time anyway.
    var MAX_ITEMS = 200;
    var items = [];
    var matches = [];
    var active = -1;
    var menu = document.createElement('ul');
    menu.className = 'autocomplete';
    menu.id = input.id + '-menu';
    menu.setAttribute('role', 'listbox');
    menu.hidden = true;
    document.body.appendChild(menu);

    input.setAttribute('role', 'combobox');
    input.setAttribute('autocomplete', 'off');
    input.setAttribute('aria-autocomplete', 'list');
    input.setAttribute('aria-controls', menu.id);
    input.setAttribute('aria-expanded', 'false');

    fetch(url).then(function(response) {
        return response.json();
    }).then(function(data) {
        items = data;
        if (document.activeElement === input) {
            search();
        }
    });

    function close() {
        menu.hidden = true;
        active = -1;
        input.setAttribute('aria-expanded', 'false');
        input.removeAttribute('aria-activedescendant');
    }

    function position() {
        var rect = input.getBoundingClientRect();
        menu.style.left = (rect.left + window.scrollX) + 'px';
        menu.style.top = (rect.bottom + window.scrollY) + 'px';
        menu.style.minWidth = rect.width + 'px';
    }

    function search() {
        var term = input.value.toLowerCase();
        if (term.length < MIN_LENGTH) {
            close();
            return;
        }
        matches = [];
        for (var i = 0; i < items.length && matches.length < MAX_ITEMS; i++) {
            if (String(items[i].label).toLowerCase().indexOf(term) !== -1
                    || String(items[i].value).toLowerCase().indexOf(term) !== -1) {
                matches.push(items[i]);
            }
        }
        menu.textContent = '';
        if (!matches.length) {
            close();
            return;
        }
        matches.forEach(function(item, index) {
            var li = document.createElement('li');
            li.id = menu.id + '-' + index;
            li.setAttribute('role', 'option');
            li.textContent = item.label;
            menu.appendChild(li);
        });
        active = -1;
        position();
        menu.hidden = false;
        menu.scrollTop = 0;
        input.setAttribute('aria-expanded', 'true');
    }

    function highlight(index) {
        var options = menu.children;
        if (active >= 0 && options[active]) {
            options[active].removeAttribute('aria-selected');
        }
        active = index;
        options[active].setAttribute('aria-selected', 'true');
        options[active].scrollIntoView({block: 'nearest'});
        input.setAttribute('aria-activedescendant', options[active].id);
    }

    function choose(index) {
        var item = matches[index];
        close();
        input.value = item.label;
        select(item);
    }

    input.addEventListener('input', search);
    input.addEventListener('blur', close);
    input.addEventListener('keydown', function(event) {
        if (menu.hidden) {
            if (event.key === 'ArrowDown') {
                search();
            }
            return;
        }
        if (event.key === 'ArrowDown') {
            event.preventDefault();
            highlight((active + 1) % matches.length);
        } else if (event.key === 'ArrowUp') {
            event.preventDefault();
            highlight(active <= 0 ? matches.length - 1 : active - 1);
        } else if (event.key === 'Enter' && active >= 0) {
            event.preventDefault();
            choose(active);
        } else if (event.key === 'Escape') {
            close();
        }
    });
    // mousedown would blur the input (closing the menu) before the click lands.
    menu.addEventListener('mousedown', function(event) {
        event.preventDefault();
    });
    menu.addEventListener('click', function(event) {
        var li = event.target.closest('li');
        if (li) {
            choose(Array.prototype.indexOf.call(menu.children, li));
        }
    });
    window.addEventListener('resize', function() {
        if (!menu.hidden) {
            position();
        }
    });
}
