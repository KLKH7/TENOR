"use client";

import { useEffect } from "react";

/** Scroll the transcript so the clicked citation's line is centred. */
export function ScrollToLine({ id }: { id: string }) {
  useEffect(() => {
    document.getElementById(id)?.scrollIntoView({ block: "center" });
  }, [id]);
  return null;
}
