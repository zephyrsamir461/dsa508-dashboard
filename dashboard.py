"""
Streaming Content Intelligence Dashboard
Streamlit + Plotly, reading live from MongoDB hosted on Azure (Cosmos DB for MongoDB or Atlas on Azure).

Run locally:   streamlit run dashboard.py
Connection:    set MONGO_URI in .env  (or .streamlit/secrets.toml as  MONGO_URI = "...")
"""
import json
import os

import pandas as pd
import plotly.express as px
import streamlit as st
from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv()
st.set_page_config(page_title="Streaming Content Intelligence", page_icon="🎬", layout="wide")


# ----------------------------------------------------------------------------- connection
def get_uri():
    try:
        return st.secrets["MONGO_URI"]
    except Exception:
        return os.getenv("MONGO_URI")


@st.cache_resource
def get_db():
    client = MongoClient(
        f"mongodb+srv://{st.secrets['MONGO_HOST']}/",
        username=st.secrets["database_reader"],
        password=st.secrets["92B3fIziYznWYz0T"],
        authSource="admin",
        serverSelectionTimeoutMS=15000,
    )

    client.admin.command("ping")
    return client[st.secrets.get("DB_NAME", "sample_mflix")]

@st.cache_data(ttl=600, show_spinner=False)
def _run(coll, pipeline_json):
    return pd.DataFrame(list(get_db()[coll].aggregate(json.loads(pipeline_json), allowDiskUse=True)))


def q(coll, pipeline):
    """Run a pipeline (cached for 10 minutes so filters feel instant)."""
    return _run(coll, json.dumps(pipeline))


@st.cache_data(ttl=3600, show_spinner=False)
def filter_options():
    d = get_db()
    genres = sorted(g for g in d.movies.distinct("genres") if g)
    rated = sorted(r for r in d.movies.distinct("rated") if r)
    yr = list(d.movies.aggregate([{"$match": {"year": {"$type": "number"}}},
                                  {"$group": {"_id": None, "lo": {"$min": "$year"}, "hi": {"$max": "$year"}}}]))[0]
    return genres, rated, int(yr["lo"]), int(yr["hi"])


# ----------------------------------------------------------------------------- sidebar filters
genres_all, rated_all, y_lo, y_hi = filter_options()
st.sidebar.title("Filters")
years = st.sidebar.slider("Release years", max(y_lo, 1920), y_hi, (1980, y_hi))
sel_genres = st.sidebar.multiselect("Genres (empty = all)", genres_all)
sel_rated = st.sidebar.multiselect("MPAA rating (empty = all)", rated_all)
min_films = st.sidebar.slider("Minimum films for rankings", 3, 100, 20)

match = {"year": {"$type": "number", "$gte": years[0], "$lte": years[1]}}
if sel_genres:
    match["genres"] = {"$in": sel_genres}
if sel_rated:
    match["rated"] = {"$in": sel_rated}
rating_match = {**match, "imdb.rating": {"$type": "number"}}

st.title("🎬 Streaming Content Intelligence")
st.caption("Live data from MongoDB on Azure · helps content, marketing and community teams decide what to acquire, promote and moderate.")

# ----------------------------------------------------------------------------- KPIs
kpi = q("movies", [{"$match": match},
                   {"$group": {"_id": None, "movies": {"$sum": 1}, "avg_rating": {"$avg": "$imdb.rating"},
                               "avg_runtime": {"$avg": "$runtime"}, "comments": {"$sum": "$num_mflix_comments"},
                               "wins": {"$sum": "$awards.wins"}}}])
if kpi.empty:
    st.warning("No movies match the current filters.")
    st.stop()
k = kpi.iloc[0]
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Movies", f"{int(k['movies']):,}")
c2.metric("Avg IMDb rating", f"{k['avg_rating']:.2f}")
c3.metric("Avg runtime", f"{k['avg_runtime']:.0f} min")
c4.metric("User comments", f"{int(k['comments']):,}")
c5.metric("Award wins", f"{int(k['wins']):,}")

tab1, tab2, tab3, tab4, tab5 = st.tabs(["📈 Trends", "🎭 Genres", "👥 People", "💬 Audience", "🏛️ Theaters"])

# ----------------------------------------------------------------------------- Trends
with tab1:
    yearly = q("movies", [{"$match": rating_match},
                          {"$group": {"_id": "$year", "avg_rating": {"$avg": "$imdb.rating"}, "movies": {"$sum": 1}}},
                          {"$sort": {"_id": 1}}]).rename(columns={"_id": "year"})
    l, r = st.columns(2)
    l.plotly_chart(px.line(yearly, x="year", y="avg_rating", markers=True, title="Average IMDb rating by year"))
    r.plotly_chart(px.bar(yearly, x="year", y="movies", title="Titles released per year"))

    rated_mix = q("movies", [{"$match": {**match, "rated": {"$type": "string"}}},
                             {"$group": {"_id": "$rated", "movies": {"$sum": 1}, "avg_rating": {"$avg": "$imdb.rating"}}},
                             {"$sort": {"movies": -1}}]).rename(columns={"_id": "rated"})
    st.plotly_chart(px.bar(rated_mix, x="rated", y="movies", color="avg_rating", color_continuous_scale="Viridis",
                           title="Catalogue by MPAA rating (colour = avg IMDb rating)"))

# ----------------------------------------------------------------------------- Genres
genre_df = q("movies", [{"$match": rating_match}, {"$unwind": "$genres"}] +
             ([{"$match": {"genres": {"$in": sel_genres}}}] if sel_genres else []) +
             [{"$group": {"_id": "$genres", "movies": {"$sum": 1}, "avg_rating": {"$avg": "$imdb.rating"},
                          "avg_votes": {"$avg": "$imdb.votes"}, "avg_comments": {"$avg": "$num_mflix_comments"}}},
              {"$match": {"movies": {"$gte": min(min_films, 20)}}}]).rename(columns={"_id": "genre"})
with tab2:
    if genre_df.empty:
        st.info("Not enough titles per genre for these filters.")
    else:
        fig = px.scatter(genre_df, x="movies", y="avg_rating", size="avg_votes", color="avg_comments", text="genre",
                         log_x=True, title="Genre positioning: volume vs quality (bubble = votes, colour = comments)",
                         color_continuous_scale="Turbo")
        fig.update_traces(textposition="top center")
        st.plotly_chart(fig)
        st.dataframe(genre_df.sort_values("avg_rating", ascending=False).round(2))

# ----------------------------------------------------------------------------- People
with tab3:
    l, r = st.columns(2)
    dirs = q("movies", [{"$match": rating_match}, {"$unwind": "$directors"},
                        {"$group": {"_id": "$directors", "films": {"$sum": 1}, "avg_rating": {"$avg": "$imdb.rating"}}},
                        {"$match": {"films": {"$gte": max(3, min_films // 4)}}},
                        {"$sort": {"avg_rating": -1}}, {"$limit": 15}]).rename(columns={"_id": "director"})
    if not dirs.empty:
        l.plotly_chart(px.bar(dirs.sort_values("avg_rating"), x="avg_rating", y="director", orientation="h",
                              hover_data=["films"], title="Highest-rated directors (min films applied)"))
    cast = q("movies", [{"$match": rating_match}, {"$unwind": "$cast"},
                        {"$group": {"_id": "$cast", "films": {"$sum": 1}, "avg_rating": {"$avg": "$imdb.rating"}}},
                        {"$sort": {"films": -1}}, {"$limit": 15}]).rename(columns={"_id": "actor"})
    if not cast.empty:
        r.plotly_chart(px.bar(cast.sort_values("films"), x="films", y="actor", orientation="h", color="avg_rating",
                              color_continuous_scale="Viridis", title="Most frequent cast members"))

# ----------------------------------------------------------------------------- Audience
with tab4:
    l, r = st.columns(2)
    hot = q("movies", [{"$match": match}, {"$sort": {"num_mflix_comments": -1}}, {"$limit": 15},
                       {"$project": {"_id": 0, "title": 1, "year": 1, "comments": "$num_mflix_comments", "imdb": "$imdb.rating"}}])
    l.subheader("Most-discussed titles")
    l.dataframe(hot)

    timeline = q("comments", [{"$group": {"_id": {"$year": "$date"}, "comments": {"$sum": 1}}},
                              {"$sort": {"_id": 1}}]).rename(columns={"_id": "year"})
    r.plotly_chart(px.area(timeline, x="year", y="comments", title="Community activity: comments per year (all titles)"))

    both = q("movies", [{"$match": {**rating_match, "tomatoes.viewer.rating": {"$type": "number"}}},
                        {"$sample": {"size": 1500}},
                        {"$project": {"_id": 0, "title": 1, "imdb": "$imdb.rating", "viewer": "$tomatoes.viewer.rating"}}])
    if not both.empty:
        st.plotly_chart(px.scatter(both, x="imdb", y="viewer", hover_name="title", opacity=0.5,
                                   title="Do audiences agree across platforms? IMDb (0-10) vs Rotten Tomatoes viewers (0-5)"))

    top_users = q("comments", [{"$group": {"_id": "$name", "comments": {"$sum": 1}}},
                               {"$sort": {"comments": -1}}, {"$limit": 10}]).rename(columns={"_id": "user"})
    st.plotly_chart(px.bar(top_users.sort_values("comments"), x="comments", y="user", orientation="h",
                           title="Power users (candidates for community-ambassador programs)"))

# ----------------------------------------------------------------------------- Theaters
with tab5:
    states = q("theaters", [{"$group": {"_id": "$location.address.state", "theaters": {"$sum": 1}}},
                            {"$sort": {"theaters": -1}}, {"$limit": 15}]).rename(columns={"_id": "state"})
    pts = q("theaters", [{"$project": {"_id": 0, "lon": {"$arrayElemAt": ["$location.geo.coordinates", 0]},
                                       "lat": {"$arrayElemAt": ["$location.geo.coordinates", 1]}}}])
    l, r = st.columns(2)
    l.plotly_chart(px.bar(states, x="state", y="theaters", title="Theaters by state (top 15)"))
    r.subheader("Theater locations")
    r.map(pts)

# ----------------------------------------------------------------------------- insights
st.divider()
st.subheader("Auto-generated insights for the current selection")
if not genre_df.empty:
    best = genre_df.sort_values("avg_rating", ascending=False).iloc[0]
    busiest = genre_df.sort_values("movies", ascending=False).iloc[0]
    talk = genre_df.sort_values("avg_comments", ascending=False).iloc[0]
    st.markdown(
        f"- **Quality leader:** {best['genre']} (avg {best['avg_rating']:.2f}, {int(best['movies'])} titles).\n"
        f"- **Crowded space:** {busiest['genre']} has the most titles ({int(busiest['movies'])}) - high competition for attention.\n"
        f"- **Conversation driver:** {talk['genre']} earns the most comments per title ({talk['avg_comments']:.1f}) - strong for engagement campaigns."
    )
st.caption("Data source: MongoDB on Azure · collections: movies, comments, users, theaters")
