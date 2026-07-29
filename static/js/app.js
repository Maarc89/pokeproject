/**
 * Progressive enhancement only. Every page works with this file absent --
 * it adds a submit-time loading state and Pokemon name autocomplete.
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
            if (!event.persisted) {
                return;
            }
            document.querySelectorAll("button[data-original-text]").forEach(function (button) {
                button.disabled = false;
                button.removeAttribute("aria-busy");
                button.textContent = button.dataset.originalText;
            });
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
     */
    function wireSpriteFallbacks() {
        document.querySelectorAll("img.sprite").forEach(function (img) {
            img.addEventListener("error", function () {
                var placeholder = document.createElement("div");
                placeholder.className = "sprite sprite--missing";
                placeholder.style.setProperty("--sprite-size", (img.width || 96) + "px");
                placeholder.setAttribute("role", "img");
                placeholder.setAttribute("aria-label", "No sprite available for " + img.alt);
                placeholder.textContent = "?";
                img.replaceWith(placeholder);
            });
        });
    }

    wireLoadingStates();
    wireAutocomplete();
    wireSpriteFallbacks();
})();
