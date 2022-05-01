#! /usr/bin/env python
from __future__ import print_function
from GraphGeneration import *
from IsoformGeneration import *
import os, sys
import argparse
import networkx as nx
import errno
from time import time, sleep
import itertools
import tempfile
import shutil
import matplotlib
# import parsefasta
import math
import re
import subprocess
from collections import deque
from collections import defaultdict
import matplotlib.pyplot as plt
import edlib
import _pickle as pickle
from sys import stdout

from SimplifyGraph import simplifyGraph,isCyclic
from modules import create_augmented_reference, help_functions, correct_seqs  # ,align


def eprint(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)

def syncmers(seq, k, s, t ):
    window_smers = deque([hash(seq[i:i+s]) for i in range(0, k - s + 1 )])
    curr_min = min(window_smers)
    pos_min =  window_smers.index(curr_min)
    syncmers = []
    if pos_min == t:
        syncmers = [ (curr_min, 0) ]

    for i in range(k - s + 1, len(seq) - s):
        new_smer = hash(seq[i:i+s])
        # updating window
        discarded_smer = window_smers.popleft()
        window_smers.append(new_smer)

        # Make this faster by storing pos of minimum
        curr_min = min(window_smers)
        pos_min = window_smers.index(curr_min)
        if pos_min == t:
            kmer = seq[i - (k - s) : i - (k - s) + k]
            syncmers.append( (kmer,  i - (k - s) ) )


        # # we have discarded previous windows minimum s-mer, look for new minimum brute force
        # if curr_min == discarded_smer:
        #     curr_min = min(window_smers)
        #     pos_min = window_smers.index(curr_min)
        #     if pos_min == t:
        #       syncmers.append( (curr_min,  i - (k - s) ) )

        # # Previous minimum still in window, we only need to compare with the recently added kmer
        # elif new_kmer < curr_min:
        #     curr_min = new_kmer
        #     syncmers.append( (curr_min, i) )

    return syncmers


def get_kmer_minimizers(seq, k_size, w_size):
    # kmers = [seq[i:i+k_size] for i in range(len(seq)-k_size) ]
    w = w_size - k_size
    window_kmers = deque([seq[i:i + k_size] for i in range(w + 1)])
    curr_min = min(window_kmers)
    minimizers = [(curr_min, list(window_kmers).index(curr_min))]

    for i in range(w + 1, len(seq) - k_size):
        new_kmer = seq[i:i + k_size]
        # updateing window
        discarded_kmer = window_kmers.popleft()
        window_kmers.append(new_kmer)

        # we have discarded previous windows minimizer, look for new minimizer brute force
        if curr_min == discarded_kmer:
            curr_min = min(window_kmers)
            minimizers.append((curr_min, list(window_kmers).index(curr_min) + i - w))

        # Previous minimizer still in window, we only need to compare with the recently added kmer
        elif new_kmer < curr_min:
            curr_min = new_kmer
            minimizers.append((curr_min, i))

    return minimizers


def get_kmer_maximizers(seq, k_size, w_size):
    # kmers = [seq[i:i+k_size] for i in range(len(seq)-k_size) ]
    w = w_size - k_size
    window_kmers = deque([seq[i:i + k_size] for i in range(w + 1)])
    curr_min = max(window_kmers)
    minimizers = [(curr_min, list(window_kmers).index(curr_min))]

    for i in range(w + 1, len(seq) - k_size):
        new_kmer = seq[i:i + k_size]
        # updateing window
        discarded_kmer = window_kmers.popleft()
        window_kmers.append(new_kmer)

        # we have discarded previous windows minimizer, look for new minimizer brute force
        if curr_min == discarded_kmer:
            curr_min = max(window_kmers)
            minimizers.append((curr_min, list(window_kmers).index(curr_min) + i - w))

        # Previous minimizer still in window, we only need to compare with the recently added kmer
        elif new_kmer > curr_min:
            curr_min = new_kmer
            minimizers.append((curr_min, i))

    return minimizers


def get_minimizers_and_positions_compressed(reads, w, k, hash_fcn):
    # 1. homopolymenr compress read and obtain minimizers
    M = {}
    for r_id in reads:
        (acc, seq, qual) = reads[r_id]

        seq_hpol_comp = ''.join(ch for ch, _ in itertools.groupby(seq))

        if hash_fcn == "lex":
            minimizers = get_kmer_minimizers(seq_hpol_comp, k, w)
        elif hash_fcn == "rev_lex":
            minimizers = get_kmer_maximizers(seq_hpol_comp, k, w)

        indices = [i for i, (n1, n2) in enumerate(zip(seq[:-1], seq[1:])) if
                   n1 != n2]  # indicies we want to take quality values from to get quality string of homopolymer compressed read
        indices.append(len(seq) - 1)
        positions_in_non_compressed_sring = [(m, indices[p]) for m, p in minimizers]
        M[r_id] = positions_in_non_compressed_sring

    return M


def get_minimizers_and_positions(reads, w, k, hash_fcn):
    # 1. homopolymenr compress read and obtain minimizers
    M = {}
    for r_id in reads:
        (acc, seq, qual) = reads[r_id]
        if hash_fcn == "lex":
            minimizers = get_kmer_minimizers(seq, k, w)
            # s = k - 4 # roughly w=10
            # t = 4//2
            # minimizers = syncmers(seq, k, s, t )
        elif hash_fcn == "rev_lex":
            minimizers = get_kmer_maximizers(seq, k, w)

        M[r_id] = minimizers

    return M


from array import array


def get_minimizer_combinations_database(reads, M, k, x_low, x_high):  # generates the Minimizer combinations
    # M2 = defaultdict(lambda: defaultdict(list))
    M2 = defaultdict(lambda: defaultdict(lambda: array("I")))
    tmp_cnt = 0
    forbidden = 'A' * k
    for r_id in M:
        minimizers = M[r_id]
        for (m1, p1), m1_curr_spans in minimizers_comb_iterator(minimizers, k, x_low, x_high):
            for (m2, p2) in m1_curr_spans:
                if m2 == m1 == forbidden:
                    continue

                tmp_cnt += 1
                # t = array('I', [r_id, p1, p2])
                # M2[m1][m2].append( t )
                # M2[m1][m2].append((r_id, p1, p2))

                M2[m1][m2].append(r_id)
                M2[m1][m2].append(p1)
                M2[m1][m2].append(p2)

    print(tmp_cnt, "MINIMIZER COMBINATIONS GENERATED")
    # import time
    # time.sleep(10)
    # sys.exit()

    avg_bundance = 0
    singleton_minimzer = 0
    cnt = 1
    abundants = []
    for m1 in list(M2.keys()):
        for m2 in list(M2[m1].keys()):
            if len(M2[m1][m2]) > 3:
                avg_bundance += len(M2[m1][m2]) // 3
                cnt += 1
            else:
                del M2[m1][m2]
                singleton_minimzer += 1

            if len(M2[m1][m2]) // 3 > len(reads):
                abundants.append((m1, m2, len(M2[m1][m2]) // 3))
                if m2 == forbidden:  # poly A tail
                    del M2[m1][m2]
    # for m1,m2,ab in sorted(abundants, key=lambda x: x[2], reverse=True):
    # print("Too abundant:", m1, m2, ab, len(reads))

    print("Average abundance for non-unique minimizer-combs:", avg_bundance / float(cnt))
    print("Number of singleton minimizer combinations filtered out:", singleton_minimzer)

    return M2


def minimizers_comb_iterator(minimizers, k, x_low, x_high):
    # print("read")
    for i, (m1, p1) in enumerate(minimizers[:-1]):
        m1_curr_spans = []
        for j, (m2, p2) in enumerate(minimizers[i + 1:]):
            if x_low < p2 - p1 and p2 - p1 <= x_high:
                m1_curr_spans.append((m2, p2))
                # yield (m1,p1), (m2, p2)
            elif p2 - p1 > x_high:
                break
        yield (m1, p1), m1_curr_spans[::-1]


def fill_p2(p, all_intervals_sorted_by_finish):
    stop_to_max_j = {stop: j for j, (start, stop, w, _) in enumerate(all_intervals_sorted_by_finish)}
    all_choord_to_max_j = []
    j_max = 0
    for i in range(0, all_intervals_sorted_by_finish[-1][1]+1):
        if i in stop_to_max_j:
            j_max = stop_to_max_j[i]

        all_choord_to_max_j.append(j_max)

    for j, (start, stop, w, _) in enumerate(all_intervals_sorted_by_finish):
        j_max = all_choord_to_max_j[start]
        p.append(j_max)
    return p


def solve_WIS(all_intervals_sorted_by_finish):
    # print("instance size", len(all_intervals_sorted_by_finish))
    # p = [None]
    # fill_p(p, all_intervals_sorted_by_finish)
    p = [None]
    fill_p2(p, all_intervals_sorted_by_finish)
    # if p != p2:
    #     print(p)
    #     print(p2)
    # assert p == p2
    epsilon=0.0001
    v = [None] + [(w-1) * (stop - start+epsilon) for (start, stop, w, _) in all_intervals_sorted_by_finish]
    OPT = [0]
    for j in range(1, len(all_intervals_sorted_by_finish) + 1):
        OPT.append(max(v[j] + OPT[p[j]], OPT[j - 1]))

    # assert len(p) == len(all_intervals_sorted_by_finish) + 1 == len(v) == len(OPT)

    # Find solution
    opt_indicies = []
    j = len(all_intervals_sorted_by_finish)
    while j >= 0:
        if j == 0:
            break
        if v[j] + OPT[p[j]] > OPT[j - 1]:
            opt_indicies.append(
                j - 1)  # we have shifted all indices forward by one so we neew to reduce to j -1 because of indexing in python works
            j = p[j]
        else:
            j -= 1
    return opt_indicies


def get_intervals_to_correct(opt_indicies, all_intervals_sorted_by_finish):
    intervals_to_correct = []
    for j in opt_indicies:
        start, stop, weights, instance = all_intervals_sorted_by_finish[j]
        intervals_to_correct.append((start, stop, weights, instance))

    return intervals_to_correct


def batch(dictionary, size):
    batches = []
    sub_dict = {}
    for i, (acc, seq) in enumerate(dictionary.items()):
        if i > 0 and i % size == 0:
            batches.append(sub_dict)
            sub_dict = {}
            sub_dict[acc] = seq
        else:
            sub_dict[acc] = seq

    if i / size != 0:
        sub_dict[acc] = seq
        batches.append(sub_dict)
    elif len(dictionary) == 1:
        batches.append(sub_dict)

    return batches


def edlib_alignment(x, y, k):
    # if i == 100 and j % 1000 == 0:
    #     print("Edlib processed alignments: {0}".format(j+1))

    result = edlib.align(x, y, "NW", 'dist', k)  # , task="path")
    ed = result["editDistance"]
    # locations = result["locations"]
    return ed  # , locations


from itertools import zip_longest


def grouper(iterable, n, fillvalue=None):
    "Collect data into fixed-length chunks or blocks"
    # grouper('ABCDEFG', 3, 'x') --> ABC DEF Gxx"
    args = [iter(iterable)] * n
    return zip_longest(*args, fillvalue=fillvalue)


def add_items(seqs, r_id, p1, p2):
    seqs.append(r_id)
    seqs.append(p1)
    seqs.append(p2)


def find_most_supported_span2(r_id, m1, p1, m1_curr_spans, minimizer_combinations_database, reads, all_intervals, k_size,
                             tmp_cnt, read_complexity_cnt, quality_values_database, already_computed):
    acc, seq, qual = reads[r_id]
    for (m2, p2) in m1_curr_spans:
        # print('here', p1,p2)
        relevant_reads = minimizer_combinations_database[m1][m2]
        # seqs = array("I") #{} #defaultdict(list)
        to_add = {}
        added_strings = {}
        # locations = {}
        # not_added_strings = set()
        if len(relevant_reads) // 3 >= 3:
            # cnt += 1
            ref_seq = seq[p1: p2 + k_size]
            # ref_qual = qual[p1 : p2 + k_size]
            p_error_ref = (quality_values_database[r_id][p2 + k_size] - quality_values_database[r_id][p1]) / (
                        p2 + k_size - p1)

            # seqs["curr_read"] = (p1, p2)
            # add_items(seqs, "curr_read", p1, p2)
            # add_items(seqs, r_id, p1, p2)
            to_add[r_id] = (r_id, p1, p2, 0)
            # locations[0] = len(seqs) - 3
            added_strings[ref_seq] = 0
            reads_visited = {}
            for relevant_read_id, pos1, pos2 in grouper(relevant_reads, 3):  # relevant_reads:
                if r_id == relevant_read_id:
                    continue

                read_seq = reads[relevant_read_id][1][pos1: pos2 + k_size]
                # read_qual = reads[relevant_read_id][2][pos1: pos2 + k_size]

                if read_seq == ref_seq:
                    # seqs[relevant_read_id] = (pos1, pos2)
                    # add_items(seqs, relevant_read_id, pos1, pos2)
                    to_add[relevant_read_id] = (relevant_read_id, pos1, pos2, 0)
                    # locations[relevant_read_id] = len(seqs) - 3
                    reads_visited[relevant_read_id] = 0
                    already_computed[relevant_read_id] = (p1, p2, pos1, pos2, 0)
                    continue
                elif relevant_read_id in reads_visited:
                    # print("Prev:", reads_visited[relevant_read_id])
                    # print("Act:", edlib_alignment(ref_seq, read_seq, p_error_sum_thresh*len(ref_seq)) )
                    pass
                # Implement if we see this to recompute all the aligments exact ed here instead!! Thats the only way to guarantee exactly the same
                # or maybe use this traceback to get exact: https://github.com/Martinsos/edlib/pull/132#issuecomment-522258271
                elif read_seq in added_strings:  # == ref_seq:
                    # seqs[relevant_read_id] = (pos1, pos2)
                    # add_items(seqs, relevant_read_id, pos1, pos2)
                    if relevant_read_id in to_add and added_strings[read_seq] >= to_add[relevant_read_id][3]:
                        continue
                    else:
                        to_add[relevant_read_id] = (relevant_read_id, pos1, pos2, added_strings[read_seq])

                    # locations[relevant_read_id] = len(seqs) - 3
                    reads_visited[relevant_read_id] = added_strings[read_seq]
                    already_computed[relevant_read_id] = (p1, p2, pos1, pos2, added_strings[read_seq])
                    continue

                elif relevant_read_id in already_computed:
                    curr_ref_start, curr_ref_end, curr_read_start, curr_read_end, curr_ed = already_computed[
                        relevant_read_id]
                    if (curr_read_start <= pos1 and pos2 <= curr_read_end) and (
                            curr_ref_start <= p1 and p2 <= curr_ref_end):
                        p_error_read = (quality_values_database[relevant_read_id][pos2 + k_size] -
                                        quality_values_database[relevant_read_id][pos1]) / (pos2 + k_size - pos1)
                        p_error_sum_thresh = p_error_ref + p_error_read  # curr_p_error_sum_thresh*len(ref_seq)
                        read_beg_diff = pos1 - curr_read_start
                        read_end_diff = pos2 - curr_read_end
                        ref_beg_diff = p1 - curr_ref_start
                        ref_end_diff = p2 - curr_ref_end

                        ed_est = curr_ed + math.fabs(ref_end_diff - read_end_diff) + math.fabs(
                            read_beg_diff - ref_beg_diff)
                        if 0 <= ed_est <= p_error_sum_thresh * len(ref_seq):  # < curr_p_error_sum_thresh*len(ref_seq):
                            # seqs[relevant_read_id] = (pos1, pos2)
                            # add_items(seqs, relevant_read_id, pos1, pos2)
                            if relevant_read_id in to_add and ed_est >= to_add[relevant_read_id][3]:
                                continue
                            else:
                                to_add[relevant_read_id] = (relevant_read_id, pos1, pos2, ed_est)
                            # locations[relevant_read_id] = len(seqs) - 3
                            added_strings[read_seq] = ed_est
                            reads_visited[relevant_read_id] = ed_est

                            continue

                    else:
                        pass

                p_error_read = (quality_values_database[relevant_read_id][pos2 + k_size] -
                                quality_values_database[relevant_read_id][pos1]) / (pos2 + k_size - pos1)
                p_error_sum_thresh = p_error_ref + p_error_read  # sum([D[char_] for char_ in read_qual])/len(read_qual) #+ 0.1
                editdist = edlib_alignment(ref_seq, read_seq, p_error_sum_thresh * len(ref_seq))

                tmp_cnt += 1
                if editdist >= 0:  # passing second edit distance check
                    if False:  # relevant_read_id in reads_visited: # we have already seen the minimizer combination
                        # prev_pos1, prev_pos2 = seqs[relevant_read_id]
                        prev_pos1, prev_pos2 = seqs[locations[relevant_read_id] + 1], seqs[
                            locations[relevant_read_id] + 2]
                        prev_read_seq = reads[relevant_read_id][1][prev_pos1: prev_pos2 + k_size]
                        editdist_prev = edlib_alignment(ref_seq, prev_read_seq, len(ref_seq))
                        tmp_cnt += 1
                        read_complexity_cnt += 1

                        if editdist < editdist_prev:
                            # seqs[relevant_read_id] = (pos1, pos2)
                            seqs[locations[relevant_read_id] + 1] = pos1
                            seqs[locations[relevant_read_id] + 2] = pos2
                            added_strings[read_seq] = editdist
                            reads_visited[relevant_read_id] = editdist
                            already_computed[relevant_read_id] = (p1, p2, pos1, pos2, editdist)
                            # print("REPLACED OLD MATCH")
                        # else:
                        #     # seqs[relevant_read_id] = (prev_pos1, prev_pos2)
                        #     added_strings[prev_read_seq] = editdist_prev
                        #     reads_visited[relevant_read_id] = editdist_prev
                        #     already_computed[relevant_read_id] = (p1,p2,prev_pos1, prev_pos2, editdist_prev)
                    else:
                        # seqs[relevant_read_id] = (pos1, pos2)
                        # add_items(seqs, relevant_read_id, pos1, pos2)
                        if relevant_read_id in to_add and editdist >= to_add[relevant_read_id][3]:
                            continue
                        else:
                            to_add[relevant_read_id] = (relevant_read_id, pos1, pos2, editdist)

                        # locations[relevant_read_id] = len(seqs) - 3
                        added_strings[read_seq] = editdist
                        reads_visited[relevant_read_id] = editdist
                        already_computed[relevant_read_id] = (p1, p2, pos1, pos2, editdist)

            seqs = array("I")
            # print(to_add)
            for relev_r_id in to_add:
                add_items(seqs, to_add[relev_r_id][0], to_add[relev_r_id][1], to_add[relev_r_id][2])

            all_intervals.append((p1 + k_size, p2, len(seqs) // 3, seqs))
    # del seqs
    return tmp_cnt, read_complexity_cnt
def find_most_supported_span(r_id, m1, p1, m1_curr_spans, minimizer_combinations_database, reads, all_intervals, k_size,
                             tmp_cnt, read_complexity_cnt, already_computed):
    acc, seq, qual = reads[r_id]
    for (m2, p2) in m1_curr_spans:
        # print(p1,p2)
        relevant_reads = minimizer_combinations_database[m1][m2]
        seqs = array("I")  # {} #defaultdict(list)
        added_strings = {}
        locations = {}
        # not_added_strings = set()
        if len(relevant_reads) // 3 >= 3:
            # cnt += 1
            ref_seq = seq[p1: p2 + k_size]
            # ref_qual = qual[p1 : p2 + k_size]
            # p_error_ref = (quality_values_database[r_id][p2 + k_size] - quality_values_database[r_id][p1]) / (
            #            p2 + k_size - p1)

            # seqs["curr_read"] = (p1, p2)
            # add_items(seqs, "curr_read", p1, p2)
            add_items(seqs, r_id, p1, p2)
            locations[0] = len(seqs) - 3
            added_strings[ref_seq] = 0
            reads_visited = {}
            for relevant_read_id, pos1, pos2 in grouper(relevant_reads, 3):  # relevant_reads:
                if r_id == relevant_read_id:
                    continue

                read_seq = reads[relevant_read_id][1][pos1: pos2 + k_size]
                # read_qual = reads[relevant_read_id][2][pos1: pos2 + k_size]

                if read_seq == ref_seq:
                    # seqs[relevant_read_id] = (pos1, pos2)
                    add_items(seqs, relevant_read_id, pos1, pos2)
                    locations[relevant_read_id] = len(seqs) - 3
                    reads_visited[relevant_read_id] = 0
                    already_computed[relevant_read_id] = (p1, p2, pos1, pos2, 0)
                    continue
                elif relevant_read_id in reads_visited:
                    # print("Prev:", reads_visited[relevant_read_id])
                    # print("Act:", edlib_alignment(ref_seq, read_seq, p_error_sum_thresh*len(ref_seq)) )
                    pass
                # Implement if we see this to recompute all the aligments exact ed here instead!! Thats the only way to guarantee exactly the same
                # or maybe use this traceback to get exact: https://github.com/Martinsos/edlib/pull/132#issuecomment-522258271
                elif read_seq in added_strings:  # == ref_seq:
                    # seqs[relevant_read_id] = (pos1, pos2)
                    add_items(seqs, relevant_read_id, pos1, pos2)
                    locations[relevant_read_id] = len(seqs) - 3
                    reads_visited[relevant_read_id] = added_strings[read_seq]
                    already_computed[relevant_read_id] = (p1, p2, pos1, pos2, added_strings[read_seq])
                    continue

                elif relevant_read_id in already_computed:
                    curr_ref_start, curr_ref_end, curr_read_start, curr_read_end, curr_ed = already_computed[
                        relevant_read_id]

                tmp_cnt += 1

            all_intervals.append((p1 + k_size, p2, len(seqs) // 3, seqs))
    del seqs
    return tmp_cnt, read_complexity_cnt


# Function to convert a list into a string to enable the writing of a graph (Taken from https://www.geeksforgeeks.org/python-program-to-convert-a-list-to-string/)
def listToString(s):
    # initialize an empty string
    str1 = " "

    # return string
    return (str1.join(str(s)))
D = {chr(i) : min( 10**( - (ord(chr(i)) - 33)/10.0 ), 0.79433)  for i in range(128)}
def get_qvs(reads):
    quality_values_database = {}
    for r_id in reads:
        (acc, seq, qual) = reads[r_id]
        quality_values_database[r_id] = [0]
        tmp_tot_sum = 0
        for char_ in qual:
            qv = D[char_]
            quality_values_database[r_id].append( tmp_tot_sum + qv )  #= [D[char_] for char_ in qual]
            tmp_tot_sum += qv
    return quality_values_database
#TODO:it seems simplify_graph uses wrong read ids. Fixing this should improve result quality again
def main(args):

    # start = time()
    if os.path.exists("mapping.txt"):
        os.remove("mapping.txt")
    outfolder = args.outfolder
    # read the file
    all_reads = {i + 1: (acc, seq, qual) for i, (acc, (seq, qual)) in
                 enumerate(help_functions.readfq(open(args.fastq, 'r')))}
    #eprint("Total cluster of {0} reads.".format(len(all_reads)))
    max_seqs_to_spoa = args.max_seqs_to_spoa
    if len(all_reads) <= args.exact_instance_limit:
        args.exact = True
    if args.set_w_dynamically:
        args.w = args.k + min(7, int(len(all_reads) / 500))

    #eprint("ARGUMENT SETTINGS:")
    #for key, value in args.__dict__.items():
     #   eprint("{0}: {1}".format(key, value))
        # setattr(self, key, value)
    #eprint()

    work_dir = tempfile.mkdtemp()
    print("Temporary workdirektory:", work_dir)

    # start = time()
    # corrected_reads = {}
    v_depth_ratio_threshold = args.T
    # context_depth_ratio_threshold = args.C

    #we set delta_len to be 2*k_size to make the algo feasible
    k_size = args.k
    #w = args.w
    x_high = args.xmax
    x_low = args.xmin
    new_all_reads= {}
    #print("AR",all_reads)
    for batch_id, reads in enumerate(batch(all_reads, args.max_seqs)):
        if args.set_w_dynamically:
            # Activates for 'small' clusters with less than 700 reads
            if len(reads) >= 100:
                w = min(args.w, args.k + (
                            len(reads) // 100 + 4))  # min(args.w,)  #args.w = args.k + min(7, int( len(all_reads)/500))
            else:
                w = args.k + 1 + len(reads) // 30  # min(args.w,)  #args.w = args.k + min(7, int( len(all_reads)/500))
        else:
            w = args.w
        print("Window used for batch:", w)
        is_cyclic = True
        while is_cyclic:
            graph_id = 1
            print("Working on {0} reads in a batch".format(len(reads)))
            batch_start_time = time()

            iso_abundance=args.iso_abundance
            delta_len = 2 * k_size


            hash_fcn = "lex"
            not_used=0
            # for hash_fcn in ["lex"]: # ["lex"]: #  add "rev_lex" # add four others
            if args.compression:
                minimizer_database = get_minimizers_and_positions_compressed(reads, w, k_size, hash_fcn)
            else:
                minimizer_database = get_minimizers_and_positions(reads, w, k_size, hash_fcn)

            minimizer_combinations_database = get_minimizer_combinations_database(reads, minimizer_database, k_size, x_low,
                                                                                  x_high)
            quality_values_database = get_qvs(reads)
            # print(minimizer_database)
            #if args.verbose:
             #   eprint("done creating minimizer combinations")

            # print( [ (xx, len(reads_to_M2[xx])) for xx in reads_to_M2 ])
            # sys.exit()
            # corrected_reads = {}
            # tot_errors_before = {"subs" : 0, "del": 0, "ins": 0}
            # tot_errors_after = {"subs" : 0, "del": 0, "ins": 0}
            tot_corr = 0
            # previously_corrected_regions = defaultdict(list)
            # stored_calculated_regions = defaultdict(lambda: defaultdict(int))
            tmp_cnt = 0
            all_intervals_for_graph = {}
            for r_id in sorted(reads):  # , reverse=True):
                # for r_id in reads:
                read_min_comb = [((m1, p1), m1_curr_spans) for (m1, p1), m1_curr_spans in
                                 minimizers_comb_iterator(minimizer_database[r_id], k_size, x_low, x_high)]
                # print(read_min_comb)
                # sys.exit()
                if args.exact:
                    previously_corrected_regions = defaultdict(list)
                # stored_calculated_regions = defaultdict(list)

                #  = stored_calculated_regions[r_id]
                corr_pos = []
                (acc, seq, qual) = reads[r_id]
                # print("starting correcting:", seq)

                # print(r_id, sorted(previously_corrected_regions[r_id], key=lambda x:x[1]))
                read_previously_considered_positions = set(
                    [tmp_pos for tmp_p1, tmp_p2, w_tmp, _ in previously_corrected_regions[r_id] for tmp_pos in
                     range(tmp_p1, tmp_p2)])

                #if args.verbose:
                 #   if read_previously_considered_positions:
                 #       eprint("not corrected:", [(p1_, p2_) for p1_, p2_ in
                    #                              zip(sorted(read_previously_considered_positions)[:-1],
                     #                                 sorted(read_previously_considered_positions)[1:]) if p2_ > p1_ + 1])
                  #  else:
                  #      eprint("not corrected: entire read", )

                if previously_corrected_regions[r_id]:
                    read_previously_considered_positions = set(
                        [tmp_pos for tmp_p1, tmp_p2, w_tmp, _ in previously_corrected_regions[r_id] for tmp_pos in
                         range(tmp_p1, tmp_p2)])
                    group_id = 0
                    pos_group = {}
                    sorted_corr_pos = sorted(read_previously_considered_positions)
                    for p1, p2 in zip(sorted_corr_pos[:-1], sorted_corr_pos[1:]):
                        if p2 > p1 + 1:
                            pos_group[p1] = group_id
                            group_id += 1
                            pos_group[p2] = group_id
                        else:
                            pos_group[p1] = group_id
                    if p2 == p1 + 1:
                        pos_group[p2] = group_id
                else:
                    read_previously_considered_positions = set()
                    pos_group = {}

                # if r_id in interval_database:
                #     print(len(interval_database[r_id]))
                already_computed = {}
                read_complexity_cnt = 0
                # test_cnt = 0
                # old_cnt = 0
                # test_cnt2 = 0
                all_intervals = []
                prev_visited_intervals = []

                for (m1, p1), m1_curr_spans in read_min_comb:
                    # If any position is not in range of current corrections: then correct, not just start and stop
                    not_prev_corrected_spans = [(m2, p2) for (m2, p2) in m1_curr_spans if not (
                            p1 + k_size in read_previously_considered_positions and p2 - 1 in read_previously_considered_positions)]
                    set_not_prev = set(not_prev_corrected_spans)
                    not_prev_corrected_spans2 = [(m2, p2) for (m2, p2) in m1_curr_spans if
                                                 (m2, p2) not in set_not_prev and (
                                                         p1 + k_size in pos_group and p2 - 1 in pos_group and pos_group[
                                                     p1 + k_size] != pos_group[p2 - 1])]
                    not_prev_corrected_spans += not_prev_corrected_spans2

                    if not_prev_corrected_spans:  # p1 + k_size not in read_previously_considered_positions:
                        """tmp_cnt, read_complexity_cnt = find_most_supported_span(r_id, m1, p1, not_prev_corrected_spans,
                                                                                minimizer_combinations_database, reads,
                                                                                all_intervals, k_size, tmp_cnt,
                                                                                read_complexity_cnt, already_computed)"""
                        tmp_cnt, read_complexity_cnt = find_most_supported_span2(r_id, m1, p1, not_prev_corrected_spans,
                                                                                minimizer_combinations_database, reads,
                                                                                all_intervals, k_size, tmp_cnt,
                                                                                read_complexity_cnt,quality_values_database, already_computed)

                # sys.exit()
                # if args.verbose:
                #    print("{0} edlib invoked due to repeated anchors for this read.".format(read_complexity_cnt))
                #    print(tmp_cnt, "total computed editdist.")
                #    eprint("Correcting read", r_id)

                # add prev_visited_intervals to intervals to consider
                all_intervals.extend(prev_visited_intervals)

                if previously_corrected_regions[r_id]:  # add previously corrected regions in to the solver

                    all_intervals.extend(previously_corrected_regions[r_id])
                    del previously_corrected_regions[r_id]

                if not all_intervals:
                    not_used+=1
                    eprint("Found no reads to work on")
                    corrected_seq = seq
                else:
                    all_intervals.sort(key=lambda x: x[1])
                    # print([www for (_, _,  www, _)  in all_intervals])
                    opt_indicies = solve_WIS(
                        all_intervals)  # solve Weighted Interval Scheduling here to find set of best non overlapping intervals to correct over
                    # print(opt_indicies)
                    # assert opt_indicies == opt_indicies2
                    # print(opt_indicies)
                    intervals_to_correct = get_intervals_to_correct(opt_indicies[::-1], all_intervals)
                    all_intervals_for_graph[graph_id] = intervals_to_correct
                    #print("GID",graph_id," ",acc)
                    new_all_reads[graph_id]=reads[r_id]
                    graph_id+=1

                    #if r_id == 2:
                    #    print("Intervals to correct read 60:")
                    #    print(intervals_to_correct)
                    #    print("Intervals to correct done")
                    # del all_intervals
                    # all_intervals = []

            #print("Done with batch_id:", batch_id)
            #print("Took {0} seconds.".format(time() - batch_start_time))
            # eval_sim2(corrected_seq, seq, qual, tot_errors_before, tot_errors_after)
            # if r_id == 10:
            #     sys.exit()
            # print(type(intervals_to_correct))
            with open('all_intervals.txt', 'wb') as file:
                file.write(pickle.dumps(all_intervals_for_graph))
            with open('all_reads.txt', 'wb') as file:
                file.write(pickle.dumps(all_reads))
            print("All_intervals were written into file")
            print("Skipped ",not_used," reads due to not having high enough interval abundance")
            #for r_id,intervals_to_correct in all_intervals_for_graph.items():
                #if(r_id==2):
                    #print("intervals to correct")
                    #print(intervals_to_correct)
                    #print("intervals to correct done")
            #DG, known_intervals = generateGraphfromIntervals(all_intervals_for_graph, k_size)
            #DG_old, known_intervals_old = generateGraphfromIntervals_old(all_intervals_for_graph, k_size)
            #check_graph_correctness(known_intervals,all_intervals_for_graph)
            #DG.nodes(data=True)
            #print("Number of Nodes for DG:" + str(len(DG)))
            #nodelist = list(DG.nodes)
            #print(nodelist)
            #print("number of edges in DG:" + str(DG.number_of_edges()))
            #for node in nodelist:
            #    print(node)

            #print("Number of Nodes for DG_old:" + str(len(DG_old)))
            #nodelist_old = list(DG_old.nodes)

            #print("number of edges in DG:" + str(DG_old.number_of_edges()))
            #for node in nodelist_old:
            #    print(node)
            #DG2 = generateSimpleGraphfromIntervals(all_intervals_for_graph)
            #add_Nodes(DG,args.delta_len)
            #simplifyGraph(DG,args.max_bubblesize,args.delta_len)
            # att = nx.get_node_attributes(DG, reads)
            # print("749,762 attributes: " + str(att))
            #draw_Graph(DG)
            # draw_Graph(DG2)
            # writes the graph in GraphML format into a file. Makes it easier to work with the graph later on
            #nx.write_graphml_lxml(DG, "outputgraph.graphml")
            # nx.write_graphml_lxml(DG2, "outputgraph2.graphml")
            #print("Type of Allreads")
            #print(type(all_reads))
            #print(all_intervals_for_graph.keys())
            read_len_dict = get_read_lengths(all_reads)
            DG, known_intervals, node_overview_read, reads_for_isoforms, reads_list = generateGraphfromIntervals(
                all_intervals_for_graph, k_size, delta_len, read_len_dict,new_all_reads)
            print(DG.number_of_nodes()," Nodes in our Graph")
            is_cyclic=isCyclic(DG)
            if is_cyclic:
                k_size+=1
                w+=1
                print("Regenerating Graph, increasing k to be",k_size,"and w to be ",w)
        #print("Known intervals")
        #print(known_intervals)
        print("Graph built up!")
        #print("edges with attributes:")
        #print(DG.edges(data=True))
        #print("#Nodes for DG: " + str(DG.number_of_nodes()) + " , #Edges for DG: " + str(DG.number_of_edges()))
        # edgelist = list(DG.edges.data())
        # print(edgelist)
        simplifyGraph(DG, new_all_reads,work_dir,k_size)
        #print("#Nodes for DG: " + str(DG.number_of_nodes()) + " , #Edges for DG: " + str(DG.number_of_edges()))
        #draw_Graph(DG)
        #print("finding the reads, which make up the isoforms")

        # for iso in isoform_reads:
        #print("hello")
        #print("FoundCycles:",isCyclic(DG))
        #if not (possible_cycles):
        print("Starting to generate Isoforms")
        generate_isoforms(DG, all_reads, reads_for_isoforms, work_dir, outfolder, max_seqs_to_spoa,iso_abundance)
        print("Isoforms generated")
        #else:
        #    print("found cycle. Terminate")
        #for key,value in all_reads.items():
        #    print(key,value)
        #print("Allreads written in file")
        # for path in nx.all_simple_paths(DG,"s","t"):
        #    print(path)
        # print(all_intervals_for_graph)
        # for inter in intervals_to_correct:
        #   print("Interval from "+str(inter[0])+" to "+str(inter[1]) +", supported by "+ str(inter[2])+" reads.")
        # print('Interval from ' + start + 'to '+end)
        # print(inter)
        # structure: tuple(start(int),stop(int),weighs(int), instance(array(I<-unsigned int)))

        # for r_id,interval in all_intervals_for_graph.items():
        #    if r_id==60:
        #        print(r_id,interval)
        # print(type(opt_indicies))
        # print("Hello World")
        # for index in opt_indicies:
        #    print(type(index))
        #    print(index)
    # eprint("tot_before:", tot_errors_before)
    # eprint("tot_after:", sum(tot_errors_after.values()), tot_errors_after)
    # eprint(len(corrected_reads))
    # outfile = open(os.path.join(args.outfolder, "corrected_reads.fastq"), "w")

    # for r_id, (acc, seq, qual) in corrected_reads.items():
    #    outfile.write("@{0}\n{1}\n+\n{2}\n".format(acc, seq, qual))
    # outfile.close()

    print("removing temporary workdir")
    shutil.rmtree(work_dir)

DEBUG=False
if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="De novo error correction of long-read transcriptome reads",
                                     formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument('--version', action='version', version='%(prog)s 0.0.6')

    parser.add_argument('--fastq', type=str, default=False, help='Path to input fastq file with reads')
    # parser.add_argument('--t', dest="nr_cores", type=int, default=8, help='Number of cores allocated for clustering')

    parser.add_argument('--k', type=int, default=9, help='Kmer size')
    parser.add_argument('--w', type=int, default=10, help='Window size')
    parser.add_argument('--xmin', type=int, default=18, help='Upper interval length')
    parser.add_argument('--xmax', type=int, default=80, help='Lower interval length')
    parser.add_argument('--T', type=float, default=0.1, help='Minimum fraction keeping substitution')
    # parser.add_argument('--C', type=float, default=0.05, help='Minimum fraction of keeping alternative refernece contexts')
    parser.add_argument('--exact', action="store_true", help='Get exact solution for WIS for evary read (recalculating weights for each read (much slower but slightly more accuracy,\
                                                                 not to be used for clusters with over ~500 reads)')
    parser.add_argument('--disable_numpy', action="store_true",
                        help='Do not require numpy to be installed, but this version is about 1.5x slower than with numpy.')
    parser.add_argument('--delta_len', type=int, default=3, help='Maximum length difference between two reads intervals for which they would still be merged')
    parser.add_argument('--max_seqs_to_spoa', type=int, default=200, help='Maximum number of seqs to spoa')
    parser.add_argument('--max_seqs', type=int, default=1000,
                        help='Maximum number of seqs to correct at a time (in case of large clusters).')
    parser.add_argument('--use_racon', action="store_true",
                        help='Use racon to polish consensus after spoa (more time consuming but higher accuracy).')

    parser.add_argument('--exact_instance_limit', type=int, default=0,
                        help='Activates slower exact mode for instance smaller than this limit')
    # parser.add_argument('--w_equal_k_limit', type=int, default=0,  help='Sets w=k which is slower and more memory consuming but more accurate and useful for smalled clusters.')
    parser.add_argument('--set_w_dynamically', action="store_true",
                        help='Set w = k + max(2*k, floor(cluster_size/1000)).')
    parser.add_argument('--verbose', action="store_true", help='Print various developer stats.')

    parser.add_argument('--compression', action="store_true", help='Use homopolymenr compressed reads. (Deprecated, because we will have fewer \
                                                                        minmimizer combinations to span regions in homopolymenr dense regions. Solution \
                                                                        could be to adjust upper interval legnth dynamically to guarantee a certain number of spanning intervals.')
    parser.add_argument('--outfolder', type=str, default=None,
                        help='A fasta file with transcripts that are shared between samples and have perfect illumina support.')
    parser.add_argument('--iso_abundance', type=int,default=1, help='Cutoff parameter: abundance of reads that have to support an isoform to show in results')
    # parser.set_defaults(which='main')
    args = parser.parse_args()

    if args.xmin < 2 * args.k:
        args.xmin = 2 * args.k
        eprint("xmin set to {0}".format(args.xmin))

    if len(sys.argv) == 1:
        parser.print_help()
        sys.exit()
    if not args.fastq and not args.flnc and not args.ccs:
        parser.print_help()
        sys.exit()

    if args.outfolder and not os.path.exists(args.outfolder):
        os.makedirs(args.outfolder)

    # edlib_module = 'edlib'
    # parasail_module = 'parasail'
    # if edlib_module not in sys.modules:
    #     print('You have not imported the {0} module. Only performing clustering with mapping, i.e., no alignment.'.format(edlib_module))
    # if parasail_module not in sys.modules:
    #     eprint('You have not imported the {0} module. Only performing clustering with mapping, i.e., no alignment!'.format(parasail_module))
    #     sys.exit(1)
    if 100 < args.w or args.w < args.k:
        eprint('Please specify a window of size larger or equal to k, and smaller than 100.')
        sys.exit(1)

    main(args)
