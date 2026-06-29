import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import { test } from "node:test";

const appSource = readFileSync(new URL("../src/App.tsx", import.meta.url), "utf8");
const stylesSource = readFileSync(new URL("../src/styles.css", import.meta.url), "utf8");

function componentSource(name: string, nextName: string): string {
  const start = appSource.indexOf(`function ${name}(`);
  const end = appSource.indexOf(`function ${nextName}(`);
  assert.ok(start > 0, `${name} component should exist`);
  assert.ok(end > start, `${nextName} component should follow ${name}`);
  return appSource.slice(start, end);
}

test("review-facing topic and copy labels are Chinese", () => {
  assert.match(appSource, /<span className="topic-kicker">选题方向<\/span>/);
  assert.doesNotMatch(appSource, /<span className="topic-kicker">Topic Direction<\/span>/);

  assert.match(appSource, /<span>正文<\/span>/);
  assert.match(appSource, /<span>标题<\/span>/);
  assert.match(appSource, /<span>描述<\/span>/);
  assert.doesNotMatch(appSource, /<span>Primary Text<\/span>/);
  assert.doesNotMatch(appSource, /<span>Headline<\/span>/);
  assert.doesNotMatch(appSource, /<span>Description<\/span>/);
});

test("video review is moved into the builder column and duplicated script editing is removed", () => {
  const reviewIndex = appSource.indexOf("video-review-inline-panel");
  const sideStackIndex = appSource.indexOf("video-side-stack");

  assert.ok(reviewIndex > 0, "video review panel should have an inline builder placement class");
  assert.ok(sideStackIndex > 0, "video page should still render the preview side stack");
  assert.ok(reviewIndex < sideStackIndex, "video review panel should appear before the right-side preview stack");
  assert.doesNotMatch(appSource, /className="video-overview-strip"/);
  assert.doesNotMatch(appSource, /className="storyboard-editor-details video-storyboard-editor"/);
});

test("style and camera guidance is edited only in the image script console", () => {
  const creativesViewSource = componentSource("CreativesView", "CreativeSlotCard");
  const videosViewSource = componentSource("VideosView", "DeliveryConfirmDialog");

  assert.match(creativesViewSource, /className="video-instructions script-console-input"/);
  assert.match(creativesViewSource, /placeholder="风格与镜头要求"/);
  assert.doesNotMatch(videosViewSource, /htmlFor="video-instructions"/);
  assert.doesNotMatch(videosViewSource, /id="video-instructions"/);
  assert.doesNotMatch(videosViewSource, /placeholder="视频风格或镜头要求"/);
});

test("image-page script console textareas are readable on a light editing surface", () => {
  assert.match(
    stylesSource,
    /\.script-console-textarea,\s*\.script-console-input\s*{[^}]*background:\s*#ffffff;[^}]*color:\s*#102326;/s,
  );
  assert.match(
    stylesSource,
    /\.script-console-textarea::placeholder,\s*\.script-console-input::placeholder\s*{[^}]*color:\s*#64748b;/s,
  );
});
