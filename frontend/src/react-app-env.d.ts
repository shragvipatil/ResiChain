/// <reference types="react-scripts" />

// Allow importing .png files (needed for leaflet marker icons)
declare module "*.png" {
  const content: string;
  export default content;
}