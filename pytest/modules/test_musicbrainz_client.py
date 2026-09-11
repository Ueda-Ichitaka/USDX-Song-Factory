"""Test the musicbrainz_client module."""

import unittest
from unittest.mock import patch
import musicbrainzngs
from src.modules.musicbrainz_client import (
    search_musicbrainz, lookup_musicbrainz_by_id, get_song_info)


class TestGetMusicInfos(unittest.TestCase):

    @patch('musicbrainzngs.search_artists')
    @patch('musicbrainzngs.search_recordings')
    @patch('musicbrainzngs.get_image_front')
    @patch('musicbrainzngs.get_image_list')
    @patch('musicbrainzngs.get_release_group_by_id')
    def test_get_music_infos(self, mock_get_release_group_by_id, mock_get_image_list, mock_get_image_front, mock_search_recordings, mock_search_artists):
        # Arrange
        artist = 'UltraSinger'
        title = 'That\'s Rocking! (UltrStar 2023) FULL HD'

        # Set up mock return values for the MusicBrainz API calls
        mock_search_artists.return_value = {
            'artist-list': [
                {
                    'id': 'fake_artist_id',
                    'name': artist
                }
            ]
        }

        # image_data = musicbrainzngs.get_image_front(release['id'])
        mock_get_image_front.return_value = b'fake image data'


        mock_get_image_list.return_value = {
            'images': [
                {
                    'front': True,
                    'image': 'https://example.com/image.jpg'
                }
            ]
        }

        mock_get_release_group_by_id.return_value = {
            'release-group': {
                'first-release-date': '2023-01-01'
            }
        }

        mock_search_recordings.return_value = {
            'recording-list': [
                {
                    'title': 'That\'s Rocking!',
                    'artist-credit-phrase': artist,
                    'release-list': [
                        {
                            'id': 'fake_release_id',
                            'release-group': {
                                'id': 'fake_group_id',
                                }
                        },
                    ],
                    'tag-list': [
                        {'name': 'Genre 1'},
                        {'name': 'Genre 2'},
                    ],
                    'artist-credit': [
                        {
                            'artist': {'id': 'fake_artist_id'}
                        }
                    ],
                }
            ]}

        # Call the function to test
        song_info_single_line = search_musicbrainz(f'{artist} - {title}', None) # Single line test

        # Assert the returned values
        self.assertEqual(song_info_single_line.title, 'That\'s Rocking!')
        self.assertEqual(song_info_single_line.artist, 'UltraSinger')
        self.assertEqual(song_info_single_line.year, '2023')
        self.assertEqual(song_info_single_line.genres, 'Genre 1,Genre 2,')
        self.assertEqual(song_info_single_line.cover_image_data, b'fake image data')
        self.assertEqual(song_info_single_line.cover_url, 'https://example.com/image.jpg')

        song_info_multi_line = search_musicbrainz(title, artist) # multi line test

        self.assertEqual(song_info_multi_line.title, 'That\'s Rocking!')
        self.assertEqual(song_info_multi_line.artist, 'UltraSinger')
        self.assertEqual(song_info_multi_line.year, '2023')
        self.assertEqual(song_info_multi_line.genres, 'Genre 1,Genre 2,')
        self.assertEqual(song_info_multi_line.cover_image_data, b'fake image data')
        self.assertEqual(song_info_multi_line.cover_url, 'https://example.com/image.jpg')




    @patch('musicbrainzngs.search_artists')
    @patch('musicbrainzngs.search_recordings')
    def test_get_empty_artist_music_infos(self, mock_search_recordings, mock_search_artists):
        # Arrange
        artist = 'UltraSinger'
        title = 'That\'s Rocking! (UltrStar 2023) FULL HD'

        # Set up mock return values for the MusicBrainz API calls
        mock_search_artists.return_value = {
            'artist-list': []
        }

        mock_search_recordings.return_value = {
            'recording-list': [
                {
                    'title': 'That\'s Rocking!',
                    'artist-credit-phrase': artist,
                    'release-list': [
                        {
                            'id': 'fake_release_id',
                            'release-group': {
                                'id': 'fake_group_id',
                                }
                        },
                    ],
                    'tag-list': [
                        {'name': 'Genre 1'},
                        {'name': 'Genre 2'},
                    ],
                    'artist-credit': [
                        {
                            'artist': {'id': 'fake_artist_id'}
                        }
                    ],
                }
            ]}

        # Act
        song_info_single_line = search_musicbrainz(f'{artist} - {title}', None) # Single line test

        # Assert
        self.assertEqual(song_info_single_line.title, f'{artist} - {title}')
        self.assertEqual(song_info_single_line.artist, "Unknown Artist")
        self.assertEqual(song_info_single_line.year, None)
        self.assertEqual(song_info_single_line.genres, None)
        self.assertEqual(song_info_single_line.cover_image_data, None)
        self.assertEqual(song_info_single_line.cover_url, None)

    @patch('musicbrainzngs.search_artists')
    @patch('musicbrainzngs.search_recordings')
    def test_get_empty_release_music_infos(self, mock_search_recordings, mock_search_artists):
        # Arrange
        artist = 'UltraSinger'
        title = 'That\'s Rocking! (UltrStar 2023) FULL HD'

        # Set up mock return values for the MusicBrainz API calls
        mock_search_artists.return_value = {
            'artist-list': [
                {'name': f'  {artist}  '}  # Also test leading and trailing whitespaces
            ]
        }

        mock_search_recordings.return_value = {
            'recording-list': []
        }

        # Act
        song_info_single_line = search_musicbrainz(f'{artist} - {title}', None) # Single line test

        # Assert
        self.assertEqual(song_info_single_line.title, f'{artist} - {title}')
        self.assertEqual(song_info_single_line.artist, "Unknown Artist")
        self.assertEqual(song_info_single_line.year, None)
        self.assertEqual(song_info_single_line.genres, None)
        self.assertEqual(song_info_single_line.cover_image_data, None)
        self.assertEqual(song_info_single_line.cover_url, None)


    @unittest.skip("Search with real data only test manually")
    def test_search_musicbrainz_with_real_data(self):

        # Arrange
        search_list = [
            # (search_artist, seartch_title, expected_artist, expected_title)

            (None, 't', None, None),  # this should return "Unknown artist"
            ('Căsuța noastră', 'Gică Petrescu', 'Gică Petrescu', 'Căsuța noastră'),  # Gică Petrescu - Gică Petrescu
            ("Shawn James - Through the Valley - Official Music Video", None, "Shawn James", "Through the Valley"),
            # (None, 'Corey Taylor Snuff (Acoustic)', 'Corey Taylor', 'Snuff'), # Fixme: is wrong
            # (None, 'Corey Taylor Snuff', 'Corey Taylor', 'Snuff'), # Fixme: is wrong
            # (None, 'Songs für Liam Kraftklub', 'Kraftklub', 'Songs Für Liam'), # Fixme: is wrong
            # ('Kummer feat. Fred Rabe', 'Der letzte Song (Alles wird gut)', 'Kummer feat. Fred Rabe', 'Der letzte Song (Alles wird gut)'),  # Todo: Wrong image?
            # ('Der letzte Song (Alles wird gut)', 'Kummer feat. Fred Rabe', 'Kummer feat. Fred Rabe', 'Der letzte Song (Alles wird gut)'),  # Todo: Wrong image?
            # (None, 'Der letzte Song (Alles wird gut) Kummer feat. Fred Rabe', 'Kummer feat. Fred Rabe', 'Der letzte Song (Alles wird gut)'), # Todo: Wrong image?
            # (None, 'Thats life Shawn James', 'Shawn James', 'Thats life'),  # Fixme: is wrong
            # (None, 'Gloryhole Explicit Steel Panther', 'Steel Panther', 'Gloryhole Explicit'), # Fixme: is wrong
        ]

        failed = 0
        success = 0
        count = 0
        for i, search_string in enumerate(search_list):
            artist = search_string[0]
            title = search_string[1]
            print(f"({i}) - {artist} - {title}")
            count = i

            song_info = search_musicbrainz(title, artist)
            print(f'\t{search_string}\t -> {song_info.artist}, {song_info.title}, {song_info.year}, {song_info.genres}')
            print('-------------------------------')
        print(f"Faild: {failed} | Success: {success} Count: {count}")


class TestLookupMusicbrainzById(unittest.TestCase):
    """musicbrainz_id (csv column): a direct-by-id lookup that skips the
    fuzzy search entirely - accepts either a recording or a release MBID
    (auto-detected: recording tried first, release is the fallback), and
    returns None (not an exception) when neither resolves, so the caller
    can fall back to the fuzzy search rather than aborting the song."""

    @patch('musicbrainzngs.get_image_list')
    @patch('musicbrainzngs.get_image_front')
    @patch('musicbrainzngs.get_release_group_by_id')
    @patch('musicbrainzngs.get_recording_by_id')
    def test_recording_id_resolves_directly(
            self, mock_get_recording_by_id, mock_get_release_group_by_id,
            mock_get_image_front, mock_get_image_list):
        mock_get_recording_by_id.return_value = {
            'recording': {
                'title': "That's Rocking!",
                'artist-credit-phrase': 'UltraSinger',
                'release-list': [
                    {'id': 'fake_release_id',
                     'release-group': {'id': 'fake_group_id'}},
                ],
                'tag-list': [{'name': 'Genre 1'}],
            }
        }
        mock_get_release_group_by_id.return_value = {
            'release-group': {'first-release-date': '2023-01-01'}
        }
        mock_get_image_front.return_value = b'fake image data'
        mock_get_image_list.return_value = {
            'images': [{'front': True, 'image': 'https://example.com/image.jpg'}]
        }

        info = lookup_musicbrainz_by_id('fake-recording-mbid')

        self.assertEqual(info.title, "That's Rocking!")
        self.assertEqual(info.artist, 'UltraSinger')
        self.assertEqual(info.year, '2023')
        self.assertEqual(info.genres, 'Genre 1,')
        self.assertEqual(info.cover_image_data, b'fake image data')
        mock_get_recording_by_id.assert_called_once()

    @patch('musicbrainzngs.get_image_list')
    @patch('musicbrainzngs.get_image_front')
    @patch('musicbrainzngs.get_release_by_id')
    @patch('musicbrainzngs.get_recording_by_id')
    def test_falls_back_to_release_id_when_not_a_recording(
            self, mock_get_recording_by_id, mock_get_release_by_id,
            mock_get_image_front, mock_get_image_list):
        mock_get_recording_by_id.side_effect = musicbrainzngs.ResponseError()
        mock_get_release_by_id.return_value = {
            'release': {
                'title': 'A Release Title',
                'artist-credit-phrase': 'UltraSinger',
                'date': '2019-05-01',
                'tag-list': [{'name': 'Genre 2'}],
            }
        }
        mock_get_image_front.return_value = b'release cover data'
        mock_get_image_list.return_value = {
            'images': [{'front': True, 'image': 'https://example.com/release.jpg'}]
        }

        info = lookup_musicbrainz_by_id('fake-release-mbid')

        self.assertEqual(info.title, 'A Release Title')
        self.assertEqual(info.artist, 'UltraSinger')
        self.assertEqual(info.year, '2019')
        self.assertEqual(info.genres, 'Genre 2,')
        self.assertEqual(info.cover_image_data, b'release cover data')
        mock_get_release_by_id.assert_called_once()

    @patch('musicbrainzngs.get_release_by_id')
    @patch('musicbrainzngs.get_recording_by_id')
    def test_returns_none_when_id_resolves_as_neither(
            self, mock_get_recording_by_id, mock_get_release_by_id):
        mock_get_recording_by_id.side_effect = musicbrainzngs.ResponseError()
        mock_get_release_by_id.side_effect = musicbrainzngs.ResponseError()

        self.assertIsNone(lookup_musicbrainz_by_id('totally-unknown-mbid'))

    def test_returns_none_for_empty_id(self):
        self.assertIsNone(lookup_musicbrainz_by_id(''))
        self.assertIsNone(lookup_musicbrainz_by_id(None))


class TestGetSongInfo(unittest.TestCase):
    """get_song_info(): the single entry point callers (UltraSinger.py,
    youtube.py) use - tries musicbrainz_id first when given, only falls
    back to the fuzzy search_musicbrainz() when no id was given or the id
    didn't resolve to anything."""

    @patch('musicbrainzngs.get_recording_by_id')
    @patch('musicbrainzngs.search_recordings')
    @patch('musicbrainzngs.search_artists')
    def test_skips_fuzzy_search_when_id_resolves(
            self, mock_search_artists, mock_search_recordings,
            mock_get_recording_by_id):
        mock_get_recording_by_id.return_value = {
            'recording': {'title': 'ID Title', 'artist-credit-phrase': 'ID Artist'}
        }

        info = get_song_info('Some Title', 'Some Artist', 'fake-mbid')

        self.assertEqual(info.title, 'ID Title')
        self.assertEqual(info.artist, 'ID Artist')
        mock_search_recordings.assert_not_called()
        mock_search_artists.assert_not_called()

    @patch('musicbrainzngs.get_release_by_id')
    @patch('musicbrainzngs.get_recording_by_id')
    @patch('musicbrainzngs.search_recordings')
    @patch('musicbrainzngs.search_artists')
    def test_falls_back_to_fuzzy_search_when_id_does_not_resolve(
            self, mock_search_artists, mock_search_recordings,
            mock_get_recording_by_id, mock_get_release_by_id):
        mock_get_recording_by_id.side_effect = musicbrainzngs.ResponseError()
        mock_get_release_by_id.side_effect = musicbrainzngs.ResponseError()
        mock_search_artists.return_value = {'artist-list': []}
        mock_search_recordings.return_value = {'recording-list': []}

        info = get_song_info('Some Title', 'Some Artist', 'bad-mbid')

        # no MusicBrainz match found for either the id or the fuzzy
        # search - keeps the given artist/title, same as search_musicbrainz()
        # alone would (see test_get_empty_artist_music_infos above)
        self.assertEqual(info.artist, 'Some Artist')
        mock_search_recordings.assert_called()

    @patch('musicbrainzngs.search_recordings')
    @patch('musicbrainzngs.search_artists')
    def test_uses_fuzzy_search_directly_when_no_id_given(
            self, mock_search_artists, mock_search_recordings):
        mock_search_artists.return_value = {'artist-list': []}
        mock_search_recordings.return_value = {'recording-list': []}

        get_song_info('Some Title', 'Some Artist', None)

        mock_search_recordings.assert_called()


if __name__ == '__main__':
    unittest.main()
