import { useEffect, useState } from "react";

export type Route =
  | { name: "home" }
  | { name: "feed" }
  | { name: "insight"; id: string }
  | { name: "event"; id: string }
  | { name: "analyze" }
  | { name: "profile" }
  | { name: "quality" };

export function parse(hash: string): Route {
  const parts = hash.replace(/^#\/?/, "").split("/");
  switch (parts[0]) {
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
