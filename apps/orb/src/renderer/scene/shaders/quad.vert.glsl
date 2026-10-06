attribute vec2 aPos;
uniform vec2 uRes;
uniform vec2 uC;
uniform float uR;
uniform float uQuad;
varying vec2 vQ;

void main() {
  vQ = aPos * uQuad;
  vec2 px = uC + vQ * uR;
  gl_Position = vec4(px / uRes * 2.0 - 1.0, 0.0, 1.0);
}
