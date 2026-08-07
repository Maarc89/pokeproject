/**
 * Progressive enhancement only. Every page works with this file absent -- it
 * adds a submit-time loading state, Pokemon name autocomplete, sprite
 * fallbacks, an instant normal/shiny swap, and searching without a full reload.
 *
 * Handlers are delegated at the document rather than bound per element, because
 * a live search replaces part of the DOM and anything bound directly would be
 * lost with the markup it was attached to.
 */
(function () {
    "use strict";

    /**
     * Disable the submit button and say what is happening.
     *
     * The battle lookup can take a few seconds the first time a Pokemon is
     * used (its moves have to be fetched), and without this the page just sits
     * there looking frozen.
     */
    function wireLoadingStates() {
        document.querySelectorAll("form[data-loading]").forEach(function (form) {
            form.addEventListener("submit", function () {
                if (!form.checkValidity()) {
                    return;
                }

                var button = form.querySelector("button[type=submit]");
                if (!button || button.disabled) {
                    return;
                }

                var original = button.textContent;
                button.dataset.originalText = original;
                button.textContent = button.dataset.loadingText || "Working...";
                button.setAttribute("aria-busy", "true");

                // Defer so the button is not disabled before the browser has
                // serialised the form -- a disabled control is not submitted.
                window.setTimeout(function () {
                    button.disabled = true;
                }, 0);
            });
        });

        // Restore on bfcache restore, otherwise going Back leaves a dead button.
        window.addEventListener("pageshow", function (event) {
            if (event.persisted) {
                restoreButtons();
            }
        });
    }

    /** Undo a loading state. Also needed after an in-place search, which never navigates. */
    function restoreButtons() {
        document.querySelectorAll("button[data-original-text]").forEach(function (button) {
            button.disabled = false;
            button.removeAttribute("aria-busy");
            button.textContent = button.dataset.originalText;
        });
    }

    /** Fill the shared <datalist> with names the app has already seen. */
    function wireAutocomplete() {
        var datalist = document.getElementById("pokemon-suggestions");
        if (!datalist || !document.querySelector("input[list='pokemon-suggestions']")) {
            return;
        }

        fetch("/api/pokemon-names/", { headers: { Accept: "application/json" } })
            .then(function (response) {
                if (!response.ok) {
                    throw new Error("HTTP " + response.status);
                }
                return response.json();
            })
            .then(function (data) {
                var fragment = document.createDocumentFragment();
                (data.names || []).forEach(function (name) {
                    var option = document.createElement("option");
                    option.value = name;
                    fragment.appendChild(option);
                });
                datalist.appendChild(fragment);
            })
            .catch(function () {
                // Suggestions are a convenience; typing still works without them.
            });
    }

    /**
     * Sprites are hotlinked from GitHub, so a dead URL would otherwise render
     * as a broken-image icon.
     *
     * A shiny that 404s falls back to the normal art first, and only becomes a
     * placeholder if that fails too -- losing the shiny is not losing the
     * Pokemon.
     */
    function wireSpriteFallbacks() {
        // Captured at the document, because `error` does not bubble and because
        // sprites swapped in by a live search would otherwise arrive unwired.
        document.addEventListener("error", function (event) {
            var img = event.target;
            if (!(img instanceof HTMLImageElement) || !img.classList.contains("sprite")) {
                return;
            }

            var normal = img.dataset.sprite;
            if (normal && img.getAttribute("src") !== normal) {
                img.setAttribute("src", normal);
                return;
            }

            var placeholder = document.createElement("div");
            placeholder.className = "sprite sprite--missing";
            placeholder.style.setProperty("--sprite-size", (img.width || 96) + "px");
            placeholder.setAttribute("role", "img");
            placeholder.setAttribute("aria-label", "No sprite available for " + img.alt);
            placeholder.textContent = "?";
            img.replaceWith(placeholder);
        }, true);
    }

    /**
     * Turn the normal/shiny links into an in-place swap.
     *
     * Both URLs are already on every sprite as data attributes, so this needs
     * no round trip. The address bar is kept in step with replaceState, which
     * keeps the page linkable without adding a history entry per toggle -- the
     * Back button should leave the page, not walk back through sprite flips.
     *
     * Without this file the links still work; they just reload the page.
     */
    function applyShiny(shiny) {
        document.querySelectorAll("img.sprite").forEach(function (img) {
            var wanted = shiny ? img.dataset.spriteShiny : img.dataset.sprite;
            // Some forms have no shiny art; leave those showing what they have.
            if (wanted && img.getAttribute("src") !== wanted) {
                img.setAttribute("src", wanted);
            }
        });

        document.querySelectorAll("[data-shiny-value]").forEach(function (link) {
            var active = (link.dataset.shinyValue === "1") === shiny;
            link.classList.toggle("is-active", active);
            link.setAttribute("aria-checked", active ? "true" : "false");
        });
    }

    function wireShinyToggle() {
        // Delegated, so a toggle swapped in by a live search is already live.
        document.addEventListener("click", function (event) {
            var link = event.target.closest("[data-shiny-toggle] [data-shiny-value]");
            if (!link) {
                return;
            }

            event.preventDefault();
            var shiny = link.dataset.shinyValue === "1";
            applyShiny(shiny);

            var url = new URL(window.location.href);
            if (shiny) {
                url.searchParams.set("shiny", "1");
            } else {
                url.searchParams.delete("shiny");
            }
            window.history.replaceState({}, "", url);
        });
    }

    /**
     * Search without a full page load.
     *
     * Fetches the same fragment the full page embeds (?partial=1) and swaps it
     * into the result container, then pushes the real URL so the address bar,
     * Back button and copy-paste all still work.
     *
     * Anything unexpected -- a 404, a 503, a network failure -- hands over to a
     * normal navigation, which renders the proper error page. The no-JS path is
     * that same navigation, so there is only one behaviour to get right.
     */
    function wireLiveSearch() {
        var form = document.querySelector("[data-search-form]");
        var target = document.querySelector("[data-search-result]");
        if (!form || !target || !window.history.pushState) {
            return;
        }

        function urlFor(params, partial) {
            var url = new URL(window.location.pathname, window.location.origin);
            url.search = params.toString();
            if (partial) {
                url.searchParams.set("partial", "1");
            }
            return url;
        }

        function load(params, push) {
            var pageUrl = urlFor(params, false);

            fetch(urlFor(params, true), {
                headers: { Accept: "text/html" },
                credentials: "same-origin",
            })
                .then(function (response) {
                    if (!response.ok) {
                        throw new Error("HTTP " + response.status);
                    }
                    return response.text();
                })
                .then(function (html) {
                    target.innerHTML = html;
                    restoreButtons();
                    if (push) {
                        window.history.pushState({ search: params.toString() }, "", pageUrl);
                    }
                    document.title = "Search | Pokedex";
                })
                .catch(function () {
                    // Let the browser do it properly, error page and all.
                    window.location.assign(pageUrl);
                });
        }

        form.addEventListener("submit", function (event) {
            if (!form.checkValidity()) {
                return;
            }
            event.preventDefault();
            load(new URLSearchParams(new FormData(form)), true);
        });

        window.addEventListener("popstate", function (event) {
            if (!event.state || typeof event.state.search !== "string") {
                // Not one of ours (the entry that first loaded the page).
                window.location.reload();
                return;
            }
            load(new URLSearchParams(event.state.search), false);
        });
    }

    /**
     * Show the move draft going over its limit before the form is submitted.
     *
     * Enhancement only: the server validates the count regardless, and without
     * this file the page still works -- you just find out after the round trip.
     * Nothing is disabled, because disabling a checkbox you have already ticked
     * is a good way to trap someone who wants to change their mind.
     */
    function wireDraftLimit() {
        var list = document.querySelector("[data-draft-limit]");
        if (!list) {
            return;
        }

        var limit = parseInt(list.dataset.draftLimit, 10);
        var counter = document.querySelector("[data-draft-count]");

        function update() {
            var boxes = Array.prototype.slice.call(
                list.querySelectorAll("input[type=checkbox]")
            );
            var checked = boxes.filter(function (box) { return box.checked; });
            var over = checked.length > limit;

            boxes.forEach(function (box) {
                var option = box.closest(".draft-option");
                if (option) {
                    option.classList.toggle("is-over-limit", over && box.checked);
                }
            });

            if (counter) {
                counter.textContent = over
                    ? checked.length + " selected — pick at most " + limit + "."
                    : checked.length + " of " + limit + " selected.";
            }
        }

        // Delegated at the list rather than bound per checkbox, in keeping with
        // the rest of this file.
        list.addEventListener("change", update);
        update();
    }

    wireLoadingStates();
    wireAutocomplete();
    wireSpriteFallbacks();
    wireShinyToggle();
    wireLiveSearch();
    wireDraftLimit();
})();
