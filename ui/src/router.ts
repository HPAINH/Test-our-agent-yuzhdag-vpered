import { useEffect, useState } from "react";

export type Route =
  | { name: "home" }
  | { name: "feed" }
  | { name: "insight"; id: string }
  | { name: "event"; id: string }
  | { name: "analyze" }
  | { name: "profile" }
  | { name: "quality" }
  | { name: "launch"; token: string }
  | { name: "embed"; token: string };

export function parse(hash: string): Route {
  const [path, query = ""] = hash.replace(/^#\/?/, "").split("?");
  const parts = path.split("/");
  const token = new URLSearchParams(query).get("t") || "";
  switch (parts[0]) {
    case "launch":
      return { name: "launch", token };
    case "embed":
      return { name: "embed", token };
    case "feed":
      return { name: "feed" };
    case "insight":
      return parts[1] ? { name: "insight", id: parts[1] } : { name: "feed" };
    case "event":
      return parts[1] ? { name: "event", id: parts[1] } : { name: "feed" };
    case "analyze":
      return { name: "analyze" };
    case "profile":
      return { name: "profile" };
    case "quality":
      return { name: "quality" };
    default:
      return { name: "home" };
  }
}

export function go(path: string) {
  window.location.hash = path;
}

export function replace(path: string) {
  window.history.replaceState(null, "", `${window.location.pathname}#${path}`);
  window.dispatchEvent(new HashChangeEvent("hashchange"));
}

export function useRoute(): Route {
  const [route, setRoute] = useState<Route>(() => parse(window.location.hash));
  useEffect(() => {
    const on = () => {
      setRoute(parse(window.location.hash));
      window.scrollTo(0, 0);
    };
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return route;
}
