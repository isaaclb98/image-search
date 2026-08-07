// A small, dependency-free BlurHash decoder.
//
// The indexer stores the compact BlurHash string with each image. Decoding it
// in the browser keeps the result grid visually stable while the real image
// arrives from the NAS, without another request or a client framework.

const CHARACTERS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz#$%*+,-.:;=?@[]^_{|}~";

function decode83(value) {
  let result = 0;
  for (const char of value) {
    result = result * 83 + CHARACTERS.indexOf(char);
  }
  return result;
}
function signPow(value, exp) {
  return Math.sign(value) * Math.pow(Math.abs(value), exp);
}

function sRGBToLinear(value) {
  const v = value / 255;
  return v <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
}

function linearToSRGB(value) {
  const v = Math.max(0, Math.min(1, value));
  return Math.round(
    255 * (v <= 0.0031308 ? v * 12.92 : 1.055 * Math.pow(v, 1 / 2.4) - 0.055),
  );
}

function decodeDC(value) {
  return [
    sRGBToLinear(value >> 16),
    sRGBToLinear((value >> 8) & 255),
    sRGBToLinear(value & 255),
  ];
}

function decodeAC(value, maximumValue) {
  const quantR = Math.floor(value / (19 * 19));
  const quantG = Math.floor(value / 19) % 19;
  const quantB = value % 19;
  return [
    signPow((quantR - 9) / 9, 2) * maximumValue,
    signPow((quantG - 9) / 9, 2) * maximumValue,
    signPow((quantB - 9) / 9, 2) * maximumValue,
  ];
}

export function decodeBlurHash(blurHash, width = 32, height = 32, punch = 1) {
  if (typeof blurHash !== "string" || blurHash.length < 6) {
    throw new Error("Invalid BlurHash");
  }

  const sizeFlag = decode83(blurHash.slice(0, 1));
  const numY = Math.floor(sizeFlag / 9) + 1;
  const numX = (sizeFlag % 9) + 1;
  const quantisedMaximumValue = decode83(blurHash.slice(1, 2));
  const maximumValue = (quantisedMaximumValue + 1) / 166;
  const colors = [decodeDC(decode83(blurHash.slice(2, 6)))];

  for (let i = 1; i < numX * numY; i += 1) {
    const start = 4 + i * 2;
    colors.push(decodeAC(decode83(blurHash.slice(start, start + 2)), maximumValue * punch));
  }

  const pixels = new Uint8ClampedArray(width * height * 4);
  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      const color = [0, 0, 0];
      for (let j = 0; j < numY; j += 1) {
        for (let i = 0; i < numX; i += 1) {
          const basis = Math.cos((Math.PI * x * i) / width) * Math.cos((Math.PI * y * j) / height);
          const factor = i === 0 && j === 0 ? 1 : 2;
          const component = colors[j * numX + i];
          color[0] += component[0] * basis * factor;
          color[1] += component[1] * basis * factor;
          color[2] += component[2] * basis * factor;
        }
      }
      const offset = (y * width + x) * 4;
      pixels[offset] = linearToSRGB(color[0]);
      pixels[offset + 1] = linearToSRGB(color[1]);
      pixels[offset + 2] = linearToSRGB(color[2]);
      pixels[offset + 3] = 255;
    }
  }
  return pixels;
}
