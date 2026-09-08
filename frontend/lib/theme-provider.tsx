"use client";

import { useEffect } from "react";
import { getTheme } from "./api";
import { paletteCss } from "./base24";
import { getStoredTheme, applyThemeCss } from "./theme-store";

/**
 * Re-applies the visitor's stored theme choice (localStorage) on top of the
 * SSR default after hydration. A slug the catalog no longer has (admin
 * deleted it) simply leaves the default in place.
 */
export function ThemeProvider() {
  useEffect(() => {
    const slug = getStoredTheme();
    if (!slug) return;
    getTheme(slug)
      .then((t) => applyThemeCss(paletteCss(t.palette)))
      .catch(() => {});
  }, []);

  return null;
}
