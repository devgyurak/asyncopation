// k6 load scenarios, driven by bench/run.py.
// Env: BASE_URL, PREFIX (/sync | /core | /aopt), SCENARIO, VUS, DURATION, BOOK_MIN, BOOK_MAX.
import http from "k6/http";
import { check } from "k6";

const BASE = __ENV.BASE_URL;
const PREFIX = __ENV.PREFIX;
const SCENARIO = __ENV.SCENARIO;
const BOOK_MIN = Number(__ENV.BOOK_MIN || 1);
const BOOK_MAX = Number(__ENV.BOOK_MAX || 1);

export const options = {
  vus: Number(__ENV.VUS || 20),
  duration: __ENV.DURATION || "20s",
  summaryTrendStats: ["avg", "med", "p(95)", "p(99)", "max"],
};

const bookPk = () => BOOK_MIN + Math.floor(Math.random() * (BOOK_MAX - BOOK_MIN + 1));
const json = { headers: { "Content-Type": "application/json" } };

// Every scenario must exist in all three variants of harness/site/views.py.
const SCENARIOS = {
  single: () => http.get(`${BASE}${PREFIX}/book/${bookPk()}/`),
  gather: () => http.get(`${BASE}${PREFIX}/dashboard/`),
  nplus1: () => http.get(`${BASE}${PREFIX}/books-with-authors/`),
  write: () => http.post(`${BASE}${PREFIX}/orders/`, JSON.stringify({ book: bookPk(), quantity: 1 }), json),
};

export default function () {
  const res = SCENARIOS[SCENARIO]();
  check(res, { "status is 2xx": (r) => r.status >= 200 && r.status < 300 });
}
