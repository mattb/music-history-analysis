# Analysing twenty years of listening history with Last.fm and LLMs

I've been scrobbling to [Last.fm](https://last.fm) since 2005. That's nearly 250,000 plays sitting in their database—a fairly complete record of my musical life. I've occasionally glanced at the "top artists" page, but the data has mostly just sat there. I finally built a tool to do something more interesting with it.

The starting point was a simple question: which critically-acclaimed albums have I somehow never heard? Music critics publish year-end lists every December. Cross-reference those with my listening history and I should get a nice list of blind spots to explore.

That simple idea turned into a larger project about understanding personal taste, finding critics who share it, and eventually letting Claude explore my listening history conversationally.

## Scraping the critics

I found [yearendlists.com](http://yearendlists.com), which aggregates year-end album lists from hundreds of critics. I wrote a scraper using [httpx](https://www.python-httpx.org/) and [BeautifulSoup](https://www.crummy.com/software/BeautifulSoup/) to pull down lists from 2011-2025. The data is stored as JSON per year:

```json
{
  "critic": "Pitchfork",
  "albums": [
    {"artist": "Charli XCX", "title": "Brat", "rank": 1},
    {"artist": "Cindy Lee", "title": "Diamond Jubilee", "rank": 2}
  ]
}
```

Matching these against my scrobbles turns out to be fiddly. Critics write "OK Computer" but my scrobbles might say "OK Computer (Remastered 2017)". I normalise both sides—lowercase, strip parentheticals, remove punctuation—and use fuzzy string matching with [rapidfuzz](https://github.com/maxbachmann/RapidFuzz) for the remaining edge cases.

## Which critics share my taste?

The obvious metric is overlap: how many albums did this critic list that I've also heard? But raw overlap is misleading—a critic who listed 200 albums will have higher overlap than one who listed 20, even if the smaller list is more aligned.

I use the [Szymkiewicz-Simpson coefficient](https://en.wikipedia.org/wiki/Overlap_coefficient) instead: overlap divided by the size of the smaller set. This measures true alignment regardless of list length. A critic who listed 15 albums, 10 of which I've heard, gets a higher score than one who listed 150 albums, 15 of which I've heard.

This gives me a ranked list of aligned critics. But I wanted something more nuanced—not just "which critics overlap with me" but "which critics *think about music* the way I do?"

## Two embedding spaces

Here's where it gets interesting. I built artist similarity two different ways:

**From my listening history:** Artists I play in the same week probably relate in my head. If I'm listening to Surgeon on Tuesday and Regis on Thursday, they're connected in my mental map of music. I built a co-occurrence matrix of which artists appear together in weekly windows, normalised by the geometric mean of their individual frequencies (to prevent high-play artists from dominating), and reduced it to 50 dimensions using [scikit-learn's TruncatedSVD](https://scikit-learn.org/stable/modules/generated/sklearn.decomposition.TruncatedSVD.html).

Now every artist I've listened to has a 50-dimensional vector, and I can find similar artists using cosine similarity.

**From critics' co-listing:** Artists that critics list together on year-end lists are related in critical consensus. Same technique—co-occurrence matrix, normalisation, SVD. But I also weight by rank position: a #1 album contributes more than a #50 album. The weight is `1 / log2(rank + 1)`, so #1 gets weight 1.0, #10 gets 0.30, #50 gets 0.18.

These two spaces often disagree. In my listening space, Surgeon clusters with Female, Regis, and British Murder Boys (techno producers I listen to together). In critics' space, Surgeon might cluster with Aphex Twin and Autechre—artists critics mention in the same breath, even if my personal listening habits differ.

**Bridge artists** appear in both spaces with similar neighbours. They're where my perception aligns with critical consensus—potential gateways to new discoveries that feel personally relevant AND critically vetted.

## Familiarity scoring

I needed to determine which albums I've actually "heard" versus which I've just played once or twice. The old approach was binary: "5 unique tracks, 5 plays each" meant you'd heard it. But this felt crude—playing one track 50 times is different from playing 10 tracks 5 times each.

I built a continuous familiarity score (0-1) from three weighted components:

```python
def calculate_familiarity(tracks_played, plays_per_track, total_plays):
    # Coverage: how many different tracks (capped at 10)
    coverage = min(tracks_played, 10) / 10

    # Depth: average plays per track (capped at 10)
    avg_plays = total_plays / tracks_played
    depth = min(avg_plays, 10) / 10

    # Dispersion: Shannon entropy of play distribution
    # High entropy = evenly spread plays
    # Low entropy = concentrated on few tracks
    entropy = -sum(p * log2(p) for p in play_distribution)
    max_entropy = log2(tracks_played)
    dispersion = entropy / max_entropy if max_entropy > 0 else 0

    return 0.4 * coverage + 0.4 * depth + 0.2 * dispersion
```

The dispersion component is the interesting one. It uses [Shannon entropy](https://en.wikipedia.org/wiki/Entropy_(information_theory)) to measure how evenly your plays are distributed across tracks. If you've played an album 50 times but 45 of those plays are the same track, that's low entropy—you don't really know the album, you know one song. The entropy calculation catches this pattern.

Default threshold is 0.4, which roughly corresponds to the old "5 tracks, 5 plays" rule but handles edge cases better.

## The surprise-me command

All of this powers a recommendation command with three modes:

```bash
lastfm surprise-me --mode bridge
```

**Adventurous mode** weights toward artists I've never heard. It multiplies critic alignment by a novelty factor: 1.0 for unknown artists, 0.3 for artists I've played before. Pure discovery, still filtered through aligned critics.

**Familiar mode** weights toward artists similar to my favourites in my embedding space. If a recommended artist has high cosine similarity to my top 50 artists, they get boosted. The recommendation will feel like a natural extension of existing taste.

**Bridge mode** weights toward bridge artists—those that exist in both my embedding space and the critics' space. These get a 10x boost over non-bridge artists in the weighted random selection.

The output explains why it picked something:

```
Today's Pick (bridge mode)
==================================================

  Autechre — Exai

Why this pick:

  Critic: Fact
          Your #4 aligned critic (85% match)
          Listed at #46 in their 2013 picks

  Similar to: Squarepusher, Surgeon, Aphex Twin
             (74% similarity in your listening)

  Bridge artist: Connects your taste to critical consensus
             You group with: Squarepusher, Surgeon, Aphex Twin
             Critics group with: Dedekind Cut, Gaika, Catherine Christer Hennix

  You've played Autechre 190x but never this album
```

I find the bridge explanation quite satisfying. It shows how the same artist lives in two different similarity neighbourhoods—mine and the critics'—and that they overlap enough to be a reliable recommendation.

## MusicBrainz enrichment

Scrobbles don't include release years, genres, or labels. [MusicBrainz](https://musicbrainz.org/) has all that metadata, but their API is rate-limited to 1 request per second. With thousands of albums to look up, that would take hours.

The solution: MusicBrainz publishes [complete data dumps](https://musicbrainz.org/doc/MusicBrainz_Database/Download). I download the release dump (~3GB compressed), parse the JSON, and build a local SQLite database. The schema includes:

- Release year, country, language
- Genres with occurrence counts
- Labels with catalogue numbers
- Release type (album, EP, single, compilation)

Lookups are now instant. This enables queries I couldn't do otherwise:

**Catalog vs new releases:** What percentage of my listening is to albums released this year versus back catalogue? Turns out I'm about 30% new releases, 70% back catalogue—I'm living slightly in the past.

**Genre evolution:** How has my genre distribution shifted over 20 years? I can plot this year by year and see the drift from guitar music toward electronic.

**Label loyalty:** Which record labels dominate my listening? I hadn't realised how much Warp, Raster-Noton, and Hospital Records feature until I ran the numbers.

## MCP server for LLM exploration

I added an MCP server so [Claude](https://claude.ai) can explore my listening history conversationally. [MCP (Model Context Protocol)](https://modelcontextprotocol.io/) is Anthropic's standard for exposing tools to language models.

The interesting bit is asking questions the CLI didn't anticipate. I can ask Claude: "What was my gateway into ambient music?" and it will use the tools to find when I first played ambient artists, what I was listening to around that time, and construct a narrative. Or: "I stopped listening to Radiohead around 2018. What replaced them?" Claude can identify what I discovered in 2018-2019 that might have filled that space.

This feels like a genuine new capability—not just running pre-built queries, but having a conversation about my data with an intelligence that can connect dots I wouldn't think to connect.

## Validation

I built an eval framework to check if any of this actually works. Two main tests:

**Holdout evaluation:** Train embeddings on historical data (say 2005-2022), then check if they predict artists I discovered in 2023-2024. The metric is lift over random baseline—if embeddings had no predictive power, they'd be no better than random. I'm seeing 2-3x lift, which suggests the co-occurrence structure captures something real about how taste develops.

I tested different co-occurrence windows: session-based (30-minute gaps), daily, and weekly. Weekly performed best. My theory is that weekly windows capture intentional listening choices—what you return to over a week—without the noise of daily mood swings.

**Follow-through evaluation:** Take critic recommendations from a past year. Did I eventually play those albums? This measures whether critic alignment actually predicts engagement. Critics in my top 10 by alignment have about 3x higher follow-through than critics outside the top 50.

## Other commands

The full CLI has quite a few commands. Some highlights:

```bash
# Download your Last.fm history
lastfm fetch-api-key --key YOUR_API_KEY
lastfm fetch YOUR_USERNAME

# Year-in-review as HTML
lastfm review --html 2024-review.html

# Critics who listed an artist across all years
lastfm critics who-listed "Radiohead"

# Highly-acclaimed albums you've never heard
lastfm critics blind-spots --min-critics 20

# Artists you abandoned (last played in a given year)
lastfm listen abandoned --year 2018

# Musical genome visualisation (2D UMAP projection)
lastfm visualize genome --min-plays 10
```

The `listen abandoned` command is morbidly interesting. It shows artists you stopped listening to in a given year—the exits from your musical life. Combined with `listen discovered` (entry points), you can map the flow of artists through your listening history.

## Spotify support

Spotify users can request their Extended Streaming History (Settings → Privacy → Download your data), then convert it:

```bash
lastfm spotify convert ~/Downloads/my_spotify_data -o my-history.csv
lastfm --csv my-history.csv surprise-me
```

The Spotify data lacks MusicBrainz IDs, so metadata lookups fall back to artist/album name matching. It works, but the first run is slower while it builds the name-based cache.

## Limitations

A few things I'd do differently or haven't solved:

**The critics data is noisy.** yearendlists.com has some parsing issues, and critic names aren't normalised (same person might appear as "John Smith" and "John Smith, Rolling Stone"). I clean up the worst cases but there's still noise.

**Weekly windows might not be optimal for everyone.** If you only listen to music occasionally, weekly co-occurrence might be too sparse. Session-based might work better for light listeners.

**Genre matching is imperfect.** MusicBrainz genres are community-contributed and inconsistent. "Electronic" might mean anything from ambient to gabber.

**The embedding evaluation is retrospective.** I can show that embeddings predicted past discoveries, but I don't have ground truth for whether current recommendations are actually good. That would require longitudinal tracking.

## Tools used

The project is Python, using:
- [typer](https://typer.tiangolo.com/) for the CLI
- [pandas](https://pandas.pydata.org/) for data manipulation
- [scikit-learn](https://scikit-learn.org/) for SVD and cosine similarity
- [rich](https://rich.readthedocs.io/) for console formatting
- [httpx](https://www.python-httpx.org/) and [BeautifulSoup](https://www.crummy.com/software/BeautifulSoup/) for scraping
- [FastMCP](https://github.com/jlowin/fastmcp) for the MCP server
- [umap-learn](https://umap-learn.readthedocs.io/) and [datamapplot](https://datamapplot.readthedocs.io/) for the genome visualisation

Everything runs locally—no external services beyond the initial data fetches.

## What's next

I'd like to add genre-aware modes to surprise-me: "adventurous within jazz" or "bridge artists in electronic music." The MusicBrainz genre data is there, it just needs filtering.

Social comparison could be interesting too—comparing embedding spaces between friends to find bridge artists that might convert someone to a new genre.

For now though, it's quite satisfying to finally have a tool that surfaces the Autechre album I've somehow never heard despite 190 plays of other Autechre. Twenty years of data, finally doing something useful.

Code is on [GitHub](https://github.com/mattb/music-history-analysis).
