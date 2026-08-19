# image-search v2

## Aesthetic, look, vibe

- Wii menu  
- Glass  
- Clean, minimalist  
- Glassy colour tint from photos (tasteful colour bleed)

## Design Philosophy

- Minimalist.  
- No/minimal bespoke elements, design.  
- Single source of truth for primitives (such as a single button type, for instance).  
- Primitives are reused modularly.  
- Pages obey similar or the same templates.  
- As simple as it could possibly be.  
- Colour choices are absolutely minimal. Text choices are minimal. etc.

## Constraints

- Frontend must be redesigned from scratch. Absolutely no reuse of old design, code, layouts, etc.

## Tech stack

- SvelteKit  
- Typescript  
- FastAPI OpenAPI schema as the **source of truth** for the frontend API types.

## Required pages and functionality

### Top bar

- Contains tabs for each main section.

### Home page

- Search page controls to begin a search  
- A small set, 20, of For you recommended images, chosen randomly from the top 800 recommended images. This row IS the For You feature — same recommendation engine as the For You page, one surface for it on Home.

### Search

- Single text input for positive/negative prompts with toggle for positive/negative prompts.  
- Inputted prompts are displayed beneath. Darker colour for negative prompts to distinguish. Can remove each. The text input for prompts is cleared when a prompt is added.  
- An additional features collapsible section featuring:  
  - Filename filter  
  - Diversity controls  
- Search starts when ‘Search’ button is clicked.  
- Results are displayed beneath the search controls in a grid. Grid display is 5 columns maximum. 20 images loaded at a time.  
- Scroll down for infinitely loading the rest of the results.  
- When an image is left clicked, pop out and display in the same screen. Can use keyboard left and right keys to move to previous/next image in grid.  
- An image can be opened in a new tab, which displays the stand-alone photo page.  
- Will be re-usable general grid as other features, including centroid search, closest image, etc.

Photo page

- Display the image prominently.  
- Include metadata in a sidebar about the image, and include options to Like or Dislike an image, add to an album, find closest images, etc.

Random

- Randomly load images.  
- Infinite scroll.  
- Reuses grid.

For you

- Reuses grid.  
- Has diversity controls.  
- Displays recommended photos based on user’s likes and dislikes.

Albums

- Likes and Dislikes will be built-in albums. Can’t remove them.  
- Can create, edit, delete albums. Each album should have a name. No description.  
- Can choose to Search with an album: this is the centroid search.  
- Can open an album and see the images contained in the album. Can remove images from album.

Discover

- Remove this.

Centroid search

- Remove.  
- Will use Albums page for this.

Favourites

- Remove.  
- Replace in Albums page as Likes album.


Dislikes

- Remove.  
- Replace in Albums page as Dislikes album.

